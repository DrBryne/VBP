import os
import asyncio
import json
import uuid
import random
from datetime import datetime
from typing import AsyncGenerator, List, Optional, Union

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.apps import App
from google.genai import types
from google.adk.sessions import InMemorySessionService

from app.shared.models import (
    Document,
    ClinicalFinding,
    MetadataResponse,
    ClinicalFindingsResponse,
    ProcessedDocument,
    ProcessedFinding,
    MappedTerm,
    IcnpMappingResponse,
    FunctionalAreaResponse,
    ExcludedDocument,
    EvidenceValidationResponse,
    FindingValidation,
    QuoteValidation,
    WorkflowProgress,
    SynthesisResponse
)
from app.shared.tools import list_gcs_files, parse_gcs_uri
from app.shared.logging import VBPLogger
from app.shared.consolidation import group_findings, finalize_synthesis
from app.shared.taxonomy import load_valid_icnp_ids, is_valid_fo, get_default_fo
from app.shared.processing import index_document_sentences, format_indexed_text, resolve_sentence_ids, validate_taxonomy, strip_xml_tags
from app.agents.research_analyst.agent import create_research_analyst
from app.agents.term_mapper.agent import create_term_mapper
from app.agents.consolidator.agent import (
    create_quality_evaluator,
    create_evidence_validator
)

# Initialize logger
logger = VBPLogger("vbp_orchestrator")

ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/xml", "application/xml"}

class VbpWorkflowAgent(BaseAgent):
    """
    Root orchestrator for the VBP (Veiledende Behandlingsplan) Workflow.
    This BaseAgent implements a data-driven parallel workflow with isolated contexts.
    """
    def __init__(self, name: str = "vbp_workflow_agent"):
        super().__init__(name=name)
        self._research_analyst = create_research_analyst()
        self._term_mapper = create_term_mapper()
        self._evaluator = create_quality_evaluator()
        self._evidence_validator = create_evidence_validator()

    @property
    def research_analyst(self):
        return self._research_analyst

    @property
    def term_mapper(self):
        return self._term_mapper

    @property
    def evaluator(self):
        return self._evaluator

    @property
    def evidence_validator(self):
        return self._evidence_validator

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        execution_start_time = datetime.now()
        gcs_uri = ctx.session.state.get("gcs_uri")
        target_group = ctx.session.state.get("target_group")
        max_files = ctx.session.state.get("max_files")
        max_concurrency = ctx.session.state.get("max_concurrency", 10)
        
        # Extract config from message if needed
        if not gcs_uri or not target_group:
            msg_text = ""
            for msg in ctx.session.events:
                if msg.content and msg.content.role == "user" and msg.content.parts:
                    msg_text = msg.content.parts[0].text
                    break
            if msg_text:
                try:
                    config = json.loads(msg_text)
                    gcs_uri = config.get("gcs_uri", gcs_uri)
                    target_group = config.get("target_group", target_group)
                    max_files = config.get("max_files", max_files)
                    max_concurrency = config.get("max_concurrency", max_concurrency)
                except Exception: pass

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not gcs_uri or not target_group:
            err = "Missing required configuration (gcs_uri, target_group)."
            logger.error(err); yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=err)]))
            return

        logger.info(f"Starting discovery in: {gcs_uri}")
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Discovery in {gcs_uri}")]))
        
        try:
            files = list_gcs_files(gcs_uri, project_id)
            total_files_in_uri = len(files)
            if max_files: files = files[:max_files]
        except Exception as e:
            logger.error(f"Discovery failed: {e}"); yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Discovery failed: {e}")]))
            return

        if not files:
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No files found.")]))
            return

        total_files = len(files)
        logger.info(f"Processing {total_files} documents in parallel (limit: {max_concurrency})")
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Processing {total_files} documents...")]))

        semaphore = asyncio.Semaphore(max_concurrency)
        progress_queue = asyncio.Queue()
        progress_state = WorkflowProgress()
        state_lock = asyncio.Lock()
        ephemeral_session_service = InMemorySessionService()

        async def process_single_document(uri: str) -> Union[ProcessedDocument, ExcludedDocument]:
            filename = uri.split("/")[-1]
            async with semaphore:
                try:
                    await progress_queue.put(f"START: {filename}")
                    await asyncio.sleep(random.uniform(0.1, 5.0))
                    doc_session_id = str(uuid.uuid4())
                    doc_session = await ephemeral_session_service.create_session(app_name="vbp_workflow", user_id="system", session_id=doc_session_id)
                    document_context = ctx.model_copy(update={"session": doc_session, "session_service": ephemeral_session_service, "invocation_id": str(uuid.uuid4())})
                    
                    import mimetypes
                    from google.cloud import storage
                    import fitz  # PyMuPDF
                    
                    mime_type, _ = mimetypes.guess_type(uri)
                    if mime_type not in ALLOWED_MIME_TYPES:
                        async with state_lock: progress_state.completed += 1; progress_state.failed += 1
                        await progress_queue.put(f"DONE: {filename} (UNSUPPORTED TYPE: {mime_type})")
                        return ExcludedDocument(
                            source_uri=uri,
                            title=filename,
                            justification=f"The document was excluded because its file type ({mime_type}) is not supported for analysis. Only PDF, TXT, and XML are allowed."
                        )

                    static_context = types.Part.from_text(text=f"Target Group: {target_group}\n\nAnalyze the attached document.")
                    bucket_name, blob_name = parse_gcs_uri(uri)
                    storage_client = storage.Client(project=project_id)
                    
                    if mime_type == "application/pdf":
                        # Download PDF for local text extraction
                        pdf_bytes = storage_client.bucket(bucket_name).blob(blob_name).download_as_bytes()
                        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                        file_text = "".join([page.get_text() for page in doc])
                        doc.close()
                    else:
                        file_text = storage_client.bucket(bucket_name).blob(blob_name).download_as_bytes().decode('utf-8', errors='replace')
                        if mime_type in ["text/xml", "application/xml"]:
                            file_text = strip_xml_tags(file_text)

                    # --- SENTENCE INDEXING ---
                    indexed_sentences = index_document_sentences(file_text)
                    tagged_text = format_indexed_text(indexed_sentences)
                    
                    analyst_msg = types.Content(
                        role="user", 
                        parts=[
                            static_context, 
                            types.Part.from_text(text=f"Document Content (with Sentence IDs):\n{tagged_text}")
                        ]
                    )
                    doc_session.events.append(Event(author="system", content=analyst_msg))
                    
                    async for ev in self.research_analyst.run_async(document_context):
                        if ev.is_final_response() and ev.content and ev.content.parts:
                            try:
                                data_dict = json.loads(ev.content.parts[0].text)
                                if ev.author == "metadata_extractor": doc_session.state["metadata"] = MetadataResponse.model_validate(data_dict)
                                elif ev.author == "finding_extractor": doc_session.state["clinical_findings"] = ClinicalFindingsResponse.model_validate(data_dict)
                            except Exception as e: await progress_queue.put(f"ANALYST PARSE ERROR ({ev.author}): {filename} ({e})")
                    
                    metadata: MetadataResponse = doc_session.state.get("metadata")
                    clinical_findings: ClinicalFindingsResponse = doc_session.state.get("clinical_findings")
                    if not metadata:
                        metadata = MetadataResponse(source_document=Document(source_uri=uri, title=filename, publication_year=0, doi="Not found", evidence_level="Nivå 0: Ingen kategori"))
                    else:
                        metadata.source_document.source_uri = uri

                    if not clinical_findings or not clinical_findings.candidate_findings:
                        async with state_lock: progress_state.completed += 1; progress_state.no_findings += 1
                        await progress_queue.put(f"DONE: {filename} (NO FINDINGS)")
                        return ExcludedDocument(
                            source_uri=uri, 
                            title=metadata.source_document.title, 
                            justification=clinical_findings.reasoning_trace if clinical_findings else "Ingen kliniske funn identifisert."
                        )

                    # --- RESOLVE SENTENCE IDs ---
                    verified_findings = await resolve_sentence_ids(
                        clinical_findings.candidate_findings, 
                        indexed_sentences, 
                        filename, 
                        progress_state, 
                        state_lock, 
                        progress_queue
                    )

                    if not verified_findings:
                        async with state_lock: progress_state.completed += 1; progress_state.no_findings += 1
                        await progress_queue.put(f"DONE: {filename} (NO VALID CITATIONS)")
                        return ExcludedDocument(
                            source_uri=uri, 
                            title=metadata.source_document.title, 
                            justification="The document was analyzed, but all identified clinical findings were excluded because the associated sentence citations were invalid."
                        )
                    
                    clinical_findings.candidate_findings = verified_findings
                    # --- END RESOLUTION ---
                        
                    doc_id = metadata.source_document.document_id or str(uuid.uuid4())
                    metadata.source_document.document_id = doc_id
                    metadata.source_document.reasoning_trace = clinical_findings.reasoning_trace

                    lean_findings = []
                    finding_map = {}
                    for finding in clinical_findings.candidate_findings:
                        internal_id = str(uuid.uuid4()); finding_map[internal_id] = finding
                        lean_findings.append({"finding_id": internal_id, "nursing_diagnosis": finding.nursing_diagnosis, "intervention": finding.intervention, "goal": finding.goal})

                    mapper_input = json.dumps(lean_findings)
                    mapper_msg = types.Content(role="user", parts=[types.Part.from_text(text="Map these findings to ICNP and classify FO:"), types.Part.from_text(text=mapper_input)])
                    doc_session.events.append(Event(author="system", content=mapper_msg))
                    
                    try:
                        async for ev in self.term_mapper.run_async(document_context):
                            if ev.is_final_response() and ev.content and ev.content.parts:
                                try:
                                    data_dict = json.loads(ev.content.parts[0].text)
                                    if ev.author == "icnp_mapper": doc_session.state["icnp_mappings"] = IcnpMappingResponse.model_validate(data_dict)
                                    elif ev.author == "fo_classifier": doc_session.state["functional_areas"] = FunctionalAreaResponse.model_validate(data_dict)
                                except Exception as e: await progress_queue.put(f"MAPPER PARSE ERROR ({ev.author}): {filename} ({e})")
                    except Exception as e:
                        async with state_lock: progress_state.completed += 1; progress_state.failed += 1
                        await progress_queue.put(f"DONE: {filename} (MAPPER ERROR: {e})")
                        return ExcludedDocument(
                            source_uri=uri, 
                            title=metadata.source_document.title, 
                            justification="The document content was unexpected or incompatible with the standardized clinical mapping terminology."
                        )
                        
                    icnp_mappings: IcnpMappingResponse = doc_session.state.get("icnp_mappings")
                    functional_areas: FunctionalAreaResponse = doc_session.state.get("functional_areas")
                    if not functional_areas:
                        async with state_lock: progress_state.completed += 1; progress_state.failed += 1
                        await progress_queue.put(f"DONE: {filename} (MAPPER INCOMPLETE)")
                        return ExcludedDocument(
                            source_uri=uri, 
                            title=metadata.source_document.title, 
                            justification="The document content was unexpected or incompatible with the standardized clinical mapping terminology."
                        )
                        
                    icnp_lookup = {res.finding_id: res for res in icnp_mappings.results} if icnp_mappings else {}
                    fo_lookup = {res.finding_id: res.FO for res in functional_areas.results}
                    
                    # --- TAXONOMY VALIDATION ---
                    processed_findings, taxonomy_error_count = validate_taxonomy(
                        finding_map, 
                        icnp_lookup, 
                        fo_lookup, 
                        doc_id, 
                        filename, 
                        progress_state, 
                        state_lock
                    )
                    
                    if taxonomy_error_count > 0:
                        async with state_lock: progress_state.total_taxonomy_errors += taxonomy_error_count
                    # --- END TAXONOMY VALIDATION ---

                    async with state_lock: progress_state.completed += 1; progress_state.success += 1
                    await progress_queue.put(f"DONE: {filename} (SUCCESS: {len(processed_findings)} findings)")
                    return ProcessedDocument(source_document=metadata.source_document, mapped_findings=processed_findings)
                except Exception as doc_e:
                    async with state_lock: progress_state.completed += 1; progress_state.failed += 1
                    await progress_queue.put(f"DONE: {filename} (CRITICAL ERROR: {doc_e})")
                    # Use extracted title if available, otherwise filename
                    try:
                        title = metadata.source_document.title
                    except:
                        title = filename
                    return ExcludedDocument(
                        source_uri=uri, 
                        title=title, 
                        justification="The document could not be read or its format was unsupported for analysis."
                    )

        tasks = [process_single_document(f) for f in files]
        async def run_gather(): return await asyncio.gather(*tasks)
        gather_task = asyncio.create_task(run_gather())
        
        last_reported_completion = 0
        while not gather_task.done() or not progress_queue.empty():
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"[Progress] {msg}")]))
                async with state_lock: current_completed = progress_state.completed; current_success = progress_state.success
                if current_completed > last_reported_completion:
                    if current_completed % 5 == 0 or current_completed == total_files:
                        progress_msg = f"*** Overall Progress: {current_completed}/{total_files} processed ({current_success} success) ***"
                        logger.info(progress_msg); yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=progress_msg)]))
                        last_reported_completion = current_completed
                progress_queue.task_done()
            except asyncio.TimeoutError: continue

        mapped_results = await gather_task
        successful_results = [r for r in mapped_results if isinstance(r, ProcessedDocument)]
        excluded_results = [r for r in mapped_results if isinstance(r, ExcludedDocument)]
        
        if not successful_results and not excluded_results:
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No documents were successfully processed.")]))
            return

        # --- SEMANTIC QUOTE VALIDATION ---
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="Semantically validating quotes...")]))
        all_findings_to_validate = []
        for doc in successful_results:
            all_findings_to_validate.extend(doc.mapped_findings)
        
        if all_findings_to_validate:
            batch_size = 20
            validation_results = {}
            total_unsupported_dropped = 0
            
            val_tasks = []
            
            async def run_val_batch(batch, batch_idx):
                batch_payload = []
                for f in batch:
                    batch_payload.append({
                        "finding_id": f.finding_id,
                        "nursing_diagnosis": f.nursing_diagnosis,
                        "intervention": f.intervention,
                        "goal": f.goal,
                        "quotes": f.quotes
                    })
                
                valid_msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Validate these clinical findings and their quotes:\n{json.dumps(batch_payload)}")])
                val_session = await ephemeral_session_service.create_session(app_name="vbp_workflow", user_id="system", session_id=f"val-batch-{batch_idx}")
                val_ctx_batch = ctx.model_copy(update={"session": val_session, "invocation_id": str(uuid.uuid4())})
                val_session.events.append(Event(author="system", content=valid_msg))
                
                batch_results = {}
                async for ev in self.evidence_validator.run_async(val_ctx_batch):
                    if ev.is_final_response() and ev.content and ev.content.parts:
                        try:
                            val_response = EvidenceValidationResponse.model_validate(json.loads(ev.content.parts[0].text))
                            for res in val_response.results:
                                batch_results[res.finding_id] = res.quote_validations
                        except Exception as ve:
                            logger.error(f"Error parsing validation response in batch {batch_idx}: {ve}")
                return batch_results

            for i in range(0, len(all_findings_to_validate), batch_size):
                batch = all_findings_to_validate[i:i + batch_size]
                val_tasks.append(run_val_batch(batch, i // batch_size))
            
            if val_tasks:
                all_batch_results = await asyncio.gather(*val_tasks)
                for batch_dict in all_batch_results:
                    validation_results.update(batch_dict)

            new_successful_results = []
            for doc in successful_results:
                valid_doc_findings = []
                for finding in doc.mapped_findings:
                    q_val = validation_results.get(finding.finding_id)
                    if q_val:
                        kept_quotes = [v.quote for v in q_val if v.status == "kept"]
                        unsupported_in_finding = len(finding.quotes) - len(kept_quotes)
                        total_unsupported_dropped += unsupported_in_finding
                        if kept_quotes:
                            finding.quotes = kept_quotes
                            valid_doc_findings.append(finding)
                        else:
                            logger.warning(f"Finding {finding.finding_id} dropped: no semantically valid quotes.")
                            async with state_lock: progress_state.dropped_findings += 1
                    else:
                        valid_doc_findings.append(finding)
                
                if valid_doc_findings:
                    doc.mapped_findings = valid_doc_findings
                    new_successful_results.append(doc)
                else:
                    excluded_results.append(ExcludedDocument(
                        source_uri=doc.source_document.source_uri,
                        title=doc.source_document.title,
                        justification="Clinical findings were identified, but the supporting quotes were deemed clinically irrelevant during semantic validation."
                    ))
            
            successful_results = new_successful_results
            async with state_lock: progress_state.total_unsupported_quotes_dropped = total_unsupported_dropped

        # --- END SEMANTIC QUOTE VALIDATION ---

        # --- HYBRID PYTHON CONSOLIDATION ---
        logger.info(f"Consolidating {len(successful_results)} successful documents and {len(excluded_results)} excluded documents.")
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="Consolidating findings...")]))
        
        grouped_data = group_findings(successful_results)
        source_docs = [r.source_document for r in successful_results]

        # 1. Generate final quality_notes using QualityEvaluator
        quality_payload = {
            "finding_count": len(grouped_data),
            "evidence_levels": [doc.evidence_level for doc in source_docs]
        }
        quality_msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Evaluate clinical quality for these findings:\n{json.dumps(quality_payload)}")])
        qual_session = await ephemeral_session_service.create_session(app_name="vbp_workflow", user_id="system", session_id="quality-eval")
        qual_ctx = ctx.model_copy(update={"session": qual_session, "invocation_id": str(uuid.uuid4())})
        qual_session.events.append(Event(author="system", content=quality_msg))
        
        quality_notes = ""
        async for ev in self.evaluator.run_async(qual_ctx):
            if ev.is_final_response() and ev.content and ev.content.parts:
                quality_notes = ev.content.parts[0].text

        # 3. Finalize
        execution_end_time = datetime.now()
        async with state_lock:
            rectified_total = progress_state.rectified_quotes
            unsupported_total = progress_state.total_unsupported_quotes_dropped
            dropped_total = progress_state.dropped_findings
            taxonomy_total = progress_state.total_taxonomy_errors

        final_response = finalize_synthesis(
            target_group, 
            gcs_uri,
            total_files_in_uri,
            execution_start_time,
            execution_end_time,
            grouped_data, 
            quality_notes, 
            source_docs, 
            excluded_results,
            total_rectified_quotes=rectified_total,
            total_unsupported_quotes_dropped=unsupported_total,
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
