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

from app.agents.clinical_auditor.agent import create_clinical_auditor
from app.agents.clinical_extractor.agent import create_combined_extractor
from app.agents.clinical_taxonomist.agent import ClinicalTaxonomist
from app.shared.config import config
from app.shared.consolidation import finalize_synthesis, group_findings, load_taxonomy_cache, save_taxonomy_cache
from app.shared.fhir_client import FhirTerminologyClient
from app.app_utils.telemetry import setup_telemetry, track_telemetry_span
setup_telemetry()
from app.shared.logging import VBPLogger
from app.shared.models import (
    ExcludedDocument,
    ProcessedDocument,
    WorkflowProgress,
)
from app.shared.processing import (
    process_document_pipeline,
)
from app.shared.tools import list_gcs_files

# Initialize logger
logger = VBPLogger("vbp_orchestrator")

class VbpWorkflowAgent(BaseAgent):
    """
    Root orchestrator for the VBP (Veiledende Behandlingsplan) Workflow.

    This agent implements high-level coordination for massive clinical document
    analysis. It manages file discovery, task-level parallelism with
    concurrency control, and state-driven consolidation.
    """
    def __init__(self, name: str = "vbp_workflow_agent"):
        super().__init__(name=name)
        self._extractor = create_combined_extractor()
        self._taxonomist = ClinicalTaxonomist()
        self._auditor = create_clinical_auditor()
        self._fhir_client = FhirTerminologyClient()

    @property
    def extractor(self):
        """The agent responsible for finding extraction and metadata identification."""
        return self._extractor

    @property
    def taxonomist(self):
        """The agent responsible for ICNP mapping and Functional Area classification."""
        return self._taxonomist

    @property
    def auditor(self):
        """The agent responsible for multi-dimensional quality scoring."""
        return self._auditor

    @track_telemetry_span("Workflow: Orchestration")
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # Initialize terminology cache
        load_taxonomy_cache()
        # --- PHASE 1: CONFIGURATION & INITIALIZATION ---
        execution_start_time = datetime.now()
        
        # 1. Try to extract from run_config (Standard ADK 2.0 pattern)
        custom_config = getattr(ctx.run_config, "custom_config", {}) or {}
        gcs_uri = custom_config.get("gcs_uri")
        target_group = custom_config.get("target_group")
        max_files = custom_config.get("max_files")
        max_concurrency = custom_config.get("max_concurrency", 10)

        # 2. Try to extract from the triggering message in the session (Standard pattern)
        if not gcs_uri and ctx.session.events:
            for ev in ctx.session.events:
                if ev.author in ["user", "system"] and ev.content and ev.content.parts:
                    try:
                        text_content = ev.content.parts[0].text
                        if text_content and text_content.strip().startswith("{"):
                            config_dict = json.loads(text_content)
                            gcs_uri = config_dict.get("gcs_uri", gcs_uri)
                            target_group = config_dict.get("target_group", target_group)
                            max_files = config_dict.get("max_files", max_files)
                            max_concurrency = config_dict.get("max_concurrency", max_concurrency)
                            break
                    except:
                        continue

        # 3. Fallback to session state (Persistent sessions)
        if not gcs_uri: gcs_uri = ctx.session.state.get("gcs_uri")
        if not target_group: target_group = ctx.session.state.get("target_group")

        # 4. Fallback to Environment Variables (Cloud Staging Defaults)
        if not gcs_uri: gcs_uri = os.environ.get("VBP_GCS_URI")
        if not target_group: target_group = os.environ.get("VBP_TARGET_GROUP")

        # Log what we found for cloud debugging
        logger.info(f"Config extracted: gcs_uri={gcs_uri}, target_group={target_group}")

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not gcs_uri or not target_group:
            err = "Missing required configuration (gcs_uri, target_group)."
            logger.error(err)
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=err)]))
            return

        # --- PHASE 2: DOCUMENT DISCOVERY ---
        logger.info(f"Starting discovery in: {gcs_uri}")
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Discovery in {gcs_uri}")]))

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
                    progress_queue=progress_queue
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
        async def run_gather(): return await asyncio.gather(*tasks)
        gather_task = asyncio.create_task(run_gather())

        last_reported_completion = 0
        while not gather_task.done() or not progress_queue.empty():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"[Progress] {msg}")]))
                async with state_lock:
                    current_completed = progress_state.completed
                    current_success = progress_state.success
                if current_completed > last_reported_completion:
                    if current_completed % 5 == 0 or current_completed == total_files:
                        progress_msg = f"*** Overall Progress: {current_completed}/{total_files} processed ({current_success} success) ***"
                        logger.info(progress_msg)
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
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="Consolidating findings...")]))

        grouped_data = await group_findings(successful_results, self._fhir_client)
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

        logger.info("Consolidation complete. Yielding final response.")
        yield Event(
            author=self.name,
            content=types.Content(parts=[types.Part.from_text(text=final_response.model_dump_json())]),
            event_type="final_response"
        )

root_agent = VbpWorkflowAgent()
app = App(name="vbp_workflow", root_agent=root_agent)
