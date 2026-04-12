import asyncio
import json
import os
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.app_utils.telemetry import setup_telemetry, track_telemetry_span

# 1. Critical Initialization
setup_telemetry()

from app.agents.clinical_auditor.agent import create_clinical_auditor
from app.agents.clinical_extractor.agent import create_combined_extractor
from app.agents.clinical_taxonomist.agent import ClinicalTaxonomist
from app.shared.config import config
from app.shared.consolidation import (
    finalize_synthesis,
    group_findings,
    load_taxonomy_cache,
    save_taxonomy_cache,
)
from app.shared.fhir_client import FhirTerminologyClient
from app.shared.logging import VBPLogger
from app.shared.models import (
    ExcludedDocument,
    ProcessedDocument,
    WorkflowProgress,
)
from app.shared.pipeline import (
    process_document_pipeline,
)
from app.shared.tools import list_gcs_files

# Initialize logger
logger = VBPLogger("vbp_orchestrator")


class VbpWorkflowAgent(BaseAgent):
    """
    Root Orchestrator for the VBP Workflow.
    
    Provides high-level coordination for massive clinical document
    analysis. It manages file discovery, task-level parallelism with
    concurrency control, and state-driven consolidation.
    """
    def __init__(self, name: str = "vbp_workflow_agent"):
        super().__init__(name=name)
        self._extractor = create_combined_extractor()
        self._taxonomist = ClinicalTaxonomist()
        self._auditor = create_clinical_auditor()

    @property
    def extractor(self):
        """The agent responsible for metadata and clinical finding extraction."""
        return self._extractor

    @property
    def taxonomist(self):
        """The agent responsible for terminology mapping and FO classification."""
        return self._taxonomist

    @property
    def auditor(self):
        """The agent responsible for multi-dimensional quality scoring."""
        return self._auditor

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # Manual span management for the top-level orchestrator.
        from app.app_utils.telemetry import tracer
        span = tracer.start_span("Workflow: Orchestration")
        
        try:
            # A massive batch run requires overriding the default ADK 500-call limit
            ctx.run_config.max_llm_calls = 5000

            # Disable problematic OTel context switching for high concurrency
            os.environ["OTEL_PYTHON_CONTEXT_VAR_SET_NP"] = "true"

            # Initialize execution metadata
            execution_start_time = datetime.now()
            run_id = execution_start_time.strftime("%Y-%m-%d_%H-%M-%S")
            base_uri = f"{config.BASE_BUCKET}/runs/run_{run_id}"

            # Buffer for session logging to be uploaded at the end
            session_log_buffer = []
            
            # Initialize terminology cache
            load_taxonomy_cache()

            # --- PHASE 1: CONFIGURATION & INITIALIZATION ---
            # 1. Try to extract from run_config (Standard ADK 2.0 pattern)
            custom_config = getattr(ctx.run_config, "custom_config", {}) or {}
            gcs_uri = custom_config.get("gcs_uri")
            target_group = custom_config.get("target_group")
            max_files = custom_config.get("max_files")
            max_concurrency = custom_config.get("max_concurrency", 10)

            # 2. Try to extract from the triggering message in the session (Standard pattern)
            if not gcs_uri:
                # Check the direct new_message if available in the context
                msg_sources = []
                if hasattr(ctx, "new_message") and ctx.new_message:
                    msg_sources.append(ctx.new_message)
                if ctx.session.events:
                    msg_sources.extend([ev.content for ev in ctx.session.events if ev.author in ["user", "system"]])

                for content in msg_sources:
                    if content and content.parts:
                        try:
                            text_content = content.parts[0].text
                            if text_content and text_content.strip().startswith("{"):
                                config_dict = json.loads(text_content)
                                gcs_uri = config_dict.get("gcs_uri", gcs_uri)
                                target_group = config_dict.get("target_group", target_group)
                                max_files = config_dict.get("max_files", max_files)
                                max_concurrency = config_dict.get("max_concurrency", max_concurrency)
                                if gcs_uri and target_group:
                                    break
                        except Exception:
                            continue

            # 3. Fallback to session state (Persistent sessions)
            if not gcs_uri:
                gcs_uri = ctx.session.state.get("gcs_uri")
            if not target_group:
                target_group = ctx.session.state.get("target_group")

            # 4. Fallback to Environment Variables (Cloud Staging Defaults)
            if not gcs_uri:
                gcs_uri = os.environ.get("VBP_GCS_URI")
            if not target_group:
                target_group = os.environ.get("VBP_TARGET_GROUP")

            # Log what we found for cloud debugging
            logger.info(f"Config extracted: gcs_uri={gcs_uri}, target_group={target_group}")

            project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
            if not gcs_uri or not target_group:
                err = "Missing required configuration (gcs_uri, target_group)."
                logger.error(err)
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=err)]))
                return

            logger.info(f"Starting discovery in: {gcs_uri}")

            # Instantiate shared semaphore for child tasks
            taxonomy_semaphore = asyncio.Semaphore(5)

            msg = f"Discovery in {gcs_uri}"
            session_log_buffer.append(f"[Progress] {msg}")
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=msg)]))

            # --- PHASE 2: GCS DISCOVERY ---
            try:
                files = list_gcs_files(gcs_uri, project_id)
                total_files_in_uri = len(files)
                if max_files:
                    files = files[:max_files]
            except Exception as e:
                logger.error(f"Discovery failed: {e}")
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Discovery failed: {e}")]))
                return

            if not files:
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No files found.")]))
                return

            total_files = len(files)
            logger.info(f"Processing {total_files} documents in parallel (limit: {max_concurrency})")
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Processing {total_files} documents...")]))

            # --- PHASE 3: PARALLEL PIPELINE EXECUTION ---
            progress_queue = asyncio.Queue()
            progress_state = WorkflowProgress()
            state_lock = asyncio.Lock()
            ephemeral_session_service = InMemorySessionService()

            async def process_task(uri: str) -> ProcessedDocument | ExcludedDocument:
                try:
                    # Delegate detailed doc-level work to the encapsulated pipeline
                    return await process_document_pipeline(
                        uri=uri,
                        target_group=target_group,
                        project_id=project_id,
                        clinical_extractor=self.extractor,
                        clinical_taxonomist=self.taxonomist,
                        clinical_auditor=self.auditor,
                        parent_ctx=ctx,
                        ephemeral_session_service=ephemeral_session_service,
                        progress_state=progress_state,
                        state_lock=state_lock,
                        progress_queue=progress_queue,
                        taxonomy_semaphore=taxonomy_semaphore
                    )
                except Exception as e:
                    filename = uri.split("/")[-1]
                    logger.error(f"CRITICAL DOCUMENT ERROR: {filename}", error=str(e), uri=uri)
                    async with state_lock:
                        progress_state.completed += 1
                        progress_state.failed += 1
                    return ExcludedDocument(
                        source_uri=uri,
                        title=filename,
                        justification=f"A technical error occurred while processing this document: {e}"
                    )

            tasks = [process_task(f) for f in files]
            async def run_gather():
                return await asyncio.gather(*tasks)
            
            gather_task = asyncio.create_task(run_gather())

            last_reported_completion = 0
            while not gather_task.done() or not progress_queue.empty():
                try:
                    p_msg = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    session_log_buffer.append(f"[Progress] {p_msg}")
                    yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"[Progress] {p_msg}")]))
                    async with state_lock:
                        current_completed = progress_state.completed
                        current_success = progress_state.success

                        if (current_completed > last_reported_completion and current_completed % 5 == 0) or current_completed == total_files:
                            progress_msg = f"*** Overall Progress: {current_completed}/{total_files} processed ({current_success} success) ***"
                            logger.info(progress_msg)
                            session_log_buffer.append(f"[Progress] {progress_msg}")
                            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=progress_msg)]))
                            last_reported_completion = current_completed
                    progress_queue.task_done()
                except asyncio.TimeoutError:
                    continue

            mapped_results = await gather_task
            successful_results = [r for r in mapped_results if isinstance(r, ProcessedDocument)]
            excluded_results = [r for r in mapped_results if isinstance(r, ExcludedDocument)]

            if not successful_results and not excluded_results:
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No documents were successfully processed.")]))
                return

            # --- PHASE 4: CONSOLIDATION & SYNTHESIS ---
            logger.info(f"Consolidating {len(successful_results)} successful documents and {len(excluded_results)} excluded documents.")
            msg = "Consolidating findings..."
            session_log_buffer.append(f"[Progress] {msg}")
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=msg)]))

            grouped_data = await group_findings(successful_results)
            # Persist updated cache to GCS
            save_taxonomy_cache()
            source_docs = [r.source_document for r in successful_results]
            
            # 1. Finalize
            execution_end_time = datetime.now()
            async with state_lock:
                hallucinated_total = progress_state.hallucinated_citations
                dropped_total = progress_state.dropped_findings
                taxonomy_total = progress_state.total_taxonomy_errors

            final_response = finalize_synthesis(
                target_group,
                gcs_uri,
                total_files_in_uri,
                execution_start_time,
                execution_end_time,
                grouped_data,
                source_docs,
                excluded_results,
                total_hallucinated_citations=hallucinated_total,
                total_dropped_findings=dropped_total,
                total_taxonomy_errors=taxonomy_total
            )

            # --- PERMANENT STORAGE EXPORT ---
            try:
                from google.cloud import storage
                from app.report_generator.main import generate_report_from_data
                from app.shared.tools import upload_json_to_gcs

                # 1. Upload the massive JSON
                json_payload = json.loads(final_response.model_dump_json())
                json_path = f"{base_uri}/workflow_synthesis.json"
                upload_json_to_gcs(json_payload, json_path, project_id)
                logger.info(f"Successfully backed up final synthesis to {json_path}")

                # 2. Generate and Upload the Visual HTML Reports
                run_report_path = f"{base_uri}/report.html"
                latest_report_path = config.GLOBAL_REPORT_URI

                logger.info(f"Generating clinical dashboard: {latest_report_path}")
                try:
                    generate_report_from_data(final_response, latest_report_path)
                    generate_report_from_data(final_response, run_report_path)
                except Exception as report_e:
                    logger.error(f"Failed to generate HTML dashboard: {report_e}")

                # 3. Upload the Session Log text
                try:
                    bucket_name = config.BASE_BUCKET.replace("gs://", "").split("/")[0]
                    blob_name = f"runs/run_{run_id}/session.log"
                    storage_client = storage.Client(project=project_id)
                    bucket = storage_client.bucket(bucket_name)
                    blob = bucket.blob(blob_name)
                    blob.upload_from_string("\n".join(session_log_buffer), content_type="text/plain")
                except Exception as log_e:
                    logger.error(f"Failed to upload session log to GCS: {log_e}")

            except Exception as e:
                logger.error(f"Failed to backup final synthesis to GCS: {e}")

            logger.info("Consolidation complete. Yielding final response.")
            # --- FINAL RESPONSE ---
            handover_manifest = {
                "status": "success",
                "run_id": run_id,
                "target_group": target_group,
                "synthesis_uri": f"{base_uri}/workflow_synthesis.json",
                "report_url": f"https://storage.cloud.google.com/{config.BASE_BUCKET.replace('gs://', '')}/reports/latest_vbp_report.html",
                "summary": final_response.execution_summary.model_dump(mode='json')
                }
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part.from_text(text=json.dumps(handover_manifest))]),
                event_type="final_response"
            )

        finally:
            # End the telemetry span
            span.end()


root_agent = VbpWorkflowAgent()
app = App(name="vbp_workflow", root_agent=root_agent)
