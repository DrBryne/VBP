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
from app.agents.clinical_taxonomist.agent import create_combined_taxonomist
from app.shared.consolidation import finalize_synthesis, group_findings
from app.shared.fhir_client import FhirTerminologyClient
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
        self._taxonomist = create_combined_taxonomist()
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

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # --- PHASE 1: CONFIGURATION & INITIALIZATION ---
        execution_start_time = datetime.now()
        gcs_uri = ctx.session.state.get("gcs_uri")
        target_group = ctx.session.state.get("target_group")
        max_files = ctx.session.state.get("max_files")
        max_concurrency = ctx.session.state.get("max_concurrency", 10)

        # Extract config from the latest message if not in state
        if not gcs_uri or not target_group:
            if ctx.session.events:
                latest = ctx.session.events[-1]
                if latest.content and latest.content.parts:
                    try:
                        config = json.loads(latest.content.parts[0].text)
                        gcs_uri = config.get("gcs_uri", gcs_uri)
                        target_group = config.get("target_group", target_group)
                        max_files = config.get("max_files", max_files)
                        max_concurrency = config.get("max_concurrency", max_concurrency)
                    except (json.JSONDecodeError, AttributeError):
                        pass

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
        semaphore = asyncio.Semaphore(max_concurrency)
        progress_queue = asyncio.Queue()
        progress_state = WorkflowProgress()
        state_lock = asyncio.Lock()
        ephemeral_session_service = InMemorySessionService()

        async def process_task(uri: str) -> ProcessedDocument | ExcludedDocument:
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
