import asyncio
import gc
import json
import os
import uuid

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.app_utils.telemetry import track_telemetry_span
from app.shared.document_loader import (
    format_indexed_text,
    get_cache_dir,
    index_document_sentences,
    load_and_prep_document,
)
from app.shared.logging import VBPLogger
from app.shared.models import (
    AuditorResponse,
    ClinicalFinding,
    ClinicalFindingsResponse,
    DiagnosisMappingResponse,
    Document,
    ExcludedDocument,
    FunctionalAreaResponse,
    GoalMappingResponse,
    InterventionMappingResponse,
    MetadataResponse,
    ProcessedDocument,
    WorkflowProgress,
)
from app.shared.parsing_utils import safe_parse_json
from app.shared.taxonomy_validator import validate_taxonomy

logger = VBPLogger("pipeline")

ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/xml", "application/xml"}

@track_telemetry_span("Document: Resolve Sentence IDs")
async def resolve_sentence_ids(
    finding_candidates: list[ClinicalFinding],
    doc_id: str,
    filename: str,
    progress_state: WorkflowProgress,
    state_lock: asyncio.Lock,
    progress_queue: asyncio.Queue
) -> list[ClinicalFinding]:
    """Resolves citation IDs back into verbatim text with context window."""
    cache_dir = get_cache_dir()
    cache_path = os.path.join(cache_dir, f"{doc_id}_index.json")

    if not os.path.exists(cache_path):
        logger.error(f"[Indexing] Cache missing for {filename} at {cache_path}")
        return []

    with open(cache_path, encoding="utf-8") as f:
        indexed_sentences = json.load(f)

    verified_findings = []
    for finding in finding_candidates:
        unique_ids = set()
        for sid in finding.supporting_sentence_ids:
            try:
                idx = int(sid[1:])
                for offset in [-1, 0, 1]:
                    target_id = f"S{idx + offset}"
                    if target_id in indexed_sentences:
                        unique_ids.add(target_id)
                    elif offset == 0:
                        async with state_lock:
                            progress_state.hallucinated_citations += 1
                        logger.warning(f"[Indexing] Hallucinated Sentence ID '{sid}' in {filename}")
            except (ValueError, IndexError):
                async with state_lock:
                    progress_state.hallucinated_citations += 1
                logger.warning(f"[Indexing] Malformed Sentence ID '{sid}' in {filename}")

        if unique_ids:
            sorted_ids = sorted(unique_ids, key=lambda x: int(x[1:]))
            contextual_quote = " ".join([indexed_sentences[sid] for sid in sorted_ids])
            finding.quotes = [contextual_quote]

            if finding.grade_sentence_ids:
                grade_unique_ids = set()
                for sid in finding.grade_sentence_ids:
                    if sid in indexed_sentences:
                        grade_unique_ids.add(sid)
                    else:
                        logger.warning(f"[Indexing] Hallucinated GRADE Sentence ID '{sid}' in {filename}")

                if grade_unique_ids:
                    sorted_grade_ids = sorted(grade_unique_ids, key=lambda x: int(x[1:]))
                    finding.grade_quotes = [" ".join([indexed_sentences[sid] for sid in sorted_grade_ids])]
                else:
                    finding.evidence_grade = None
                    finding.recommendation_strength = None
                    finding.grade_quotes = None

            verified_findings.append(finding)
        else:
            async with state_lock:
                progress_state.dropped_findings += 1
            await progress_queue.put(f"VALIDATION: Dropped finding with no valid sentence IDs in {filename}")

    del indexed_sentences
    return verified_findings

@track_telemetry_span("Document: Pipeline Execution")
async def process_document_pipeline(
    uri: str,
    target_group: str,
    project_id: str,
    clinical_extractor: BaseAgent,
    clinical_taxonomist: BaseAgent,
    clinical_auditor: BaseAgent,
    parent_ctx: InvocationContext,
    ephemeral_session_service: InMemorySessionService,
    progress_state: WorkflowProgress,
    state_lock: asyncio.Lock,
    progress_queue: asyncio.Queue,
    taxonomy_semaphore: asyncio.Semaphore
) -> ProcessedDocument | ExcludedDocument:
    """Executes the full extraction and mapping pipeline for a single document."""
    doc_id = str(uuid.uuid4())
    try:
        filename = uri.split("/")[-1]
        logger.info(f"Processing document start: {filename}", uri=uri)
        await progress_queue.put(f"START: {filename}")

        filename, mime_type, file_text = load_and_prep_document(uri, project_id)

        if mime_type not in ALLOWED_MIME_TYPES:
            async with state_lock:
                progress_state.completed += 1
                progress_state.failed += 1
            await progress_queue.put(f"DONE: {filename} (UNSUPPORTED TYPE: {mime_type})")
            return ExcludedDocument(source_uri=uri, title=filename, justification=f"Unsupported file type: {mime_type}")

        indexed_sentences = index_document_sentences(file_text)
        tagged_text = format_indexed_text(indexed_sentences)

        cache_dir = get_cache_dir()
        cache_path = os.path.join(cache_dir, f"{doc_id}_index.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(indexed_sentences, f)

        del file_text
        del indexed_sentences

        doc_session_id = str(uuid.uuid4())
        doc_session = await ephemeral_session_service.create_session(app_name="vbp_workflow", user_id="system", session_id=doc_session_id)

        static_context = types.Part.from_text(text=f"Target Group: {target_group}\n\nAnalyze the attached document.")
        analyst_msg = types.Content(role="user", parts=[static_context, types.Part.from_text(text=f"Document Content (with Sentence IDs):\n{tagged_text}")])
        doc_session.events.append(Event(author="system", content=analyst_msg))
        del tagged_text

        pipeline_ctx = parent_ctx.model_copy(update={
            "session": doc_session,
            "invocation_id": str(uuid.uuid4()),
            "session_service": ephemeral_session_service
        })

        logger.info(f"Invoking ClinicalExtractor for: {filename}")
        async for ev in clinical_extractor.run_async(pipeline_ctx):
            if ev.is_final_response():
                data_dict = safe_parse_json(ev)
                if not data_dict:
                    continue
                try:
                    if ev.author == "metadata_extractor":
                        doc_session.state["metadata"] = MetadataResponse.model_validate(data_dict)
                    elif ev.author == "clinical_extractor":
                        doc_session.state["clinical_findings"] = ClinicalFindingsResponse.model_validate(data_dict)
                except Exception as e:
                    logger.error(f"EXTRACTOR VALIDATION ERROR: {filename}", error=str(e))

        metadata: MetadataResponse = doc_session.state.get("metadata")
        clinical_findings: ClinicalFindingsResponse = doc_session.state.get("clinical_findings")

        if not metadata:
            metadata = MetadataResponse(source_document=Document(source_uri=uri, title=filename, publication_year=0, doi="Not found", evidence_level="Nivå 0: Ingen kategori"))
        else:
            metadata.source_document.source_uri = uri

        if not clinical_findings or not clinical_findings.candidate_findings:
            async with state_lock:
                progress_state.completed += 1
                progress_state.no_findings += 1
            if os.path.exists(cache_path):
                os.remove(cache_path)
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title if metadata else filename, justification="No clinical findings found.")

        verified_findings = await resolve_sentence_ids(clinical_findings.candidate_findings, doc_id, filename, progress_state, state_lock, progress_queue)
        if os.path.exists(cache_path):
            os.remove(cache_path)

        if not verified_findings:
            async with state_lock:
                progress_state.completed += 1
                progress_state.no_findings += 1
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title, justification="No valid citations remain.")

        clinical_findings.candidate_findings = verified_findings
        doc_id_val = metadata.source_document.document_id or str(uuid.uuid4())
        metadata.source_document.document_id = doc_id_val
        metadata.source_document.reasoning_trace = clinical_findings.reasoning_trace

        # Clinical Audit
        logger.info(f"Invoking Clinical Auditor for: {filename}", finding_count=len(verified_findings))
        audit_payload = [{"finding_id": str(i), "nursing_diagnosis": f.nursing_diagnosis, "intervention": f.intervention, "goal": f.goal} for i, f in enumerate(verified_findings)]
        audit_msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Audit these clinical findings for {target_group}:"), types.Part.from_text(text=json.dumps(audit_payload))])
        doc_session.events.append(Event(author="system", content=audit_msg))

        audit_results = {}
        original_instr = clinical_auditor.instruction
        clinical_auditor.instruction = original_instr.replace("{{target_group}}", target_group)
        try:
            async for ev in clinical_auditor.run_async(pipeline_ctx):
                if ev.is_final_response():
                    data_dict = safe_parse_json(ev)
                    if data_dict:
                        try:
                            audit_resp = AuditorResponse.model_validate(data_dict)
                            audit_results = {res.finding_id: res for res in audit_resp.results}
                        except Exception as e:
                            logger.error(f"AUDITOR VALIDATION ERROR: {filename}", error=str(e))
        finally:
            clinical_auditor.instruction = original_instr

        audited_candidates = []
        for i, f in enumerate(verified_findings):
            rating = audit_results.get(str(i))
            if rating:
                weighted_score = (rating.cohesion_score * 0.4) + (rating.specificity_score * 0.3) + (rating.actionability_score * 0.3)
                if weighted_score < 5.0:
                    async with state_lock:
                        progress_state.dropped_findings += 1
                    continue
                audited_candidates.append((f, rating, weighted_score))
            else:
                audited_candidates.append((f, None, 5.0))

        if not audited_candidates:
            async with state_lock:
                progress_state.completed += 1
                progress_state.no_findings += 1
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title, justification="No findings passed quality threshold.")

        # ClinicalTaxonomist
        lean_findings = []
        finding_map = {}
        for internal_id, (finding, rating, score) in [(str(uuid.uuid4()), c) for c in audited_candidates]:
            finding_map[internal_id] = (finding, rating, score)
            lean_findings.append({"finding_id": internal_id, "nursing_diagnosis": finding.nursing_diagnosis, "intervention": finding.intervention, "goal": finding.goal})

        logger.info(f"Invoking ClinicalTaxonomist for: {filename}", finding_count=len(lean_findings))
        mapper_msg = types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(lean_findings)), types.Part.from_text(text=f"Reasoning Context:\n{metadata.source_document.reasoning_trace}")])
        doc_session.events.append(Event(author="system", content=mapper_msg))

        try:
            async with taxonomy_semaphore:
                async for ev in clinical_taxonomist.run_async(pipeline_ctx):
                    if ev.is_final_response():
                        data_dict = safe_parse_json(ev)
                        if not data_dict:
                            continue
                        try:
                            if ev.author == "diagnosis_taxonomist":
                                doc_session.state["diagnosis_mappings"] = DiagnosisMappingResponse.model_validate(data_dict)
                            elif ev.author == "intervention_taxonomist":
                                doc_session.state["intervention_mappings"] = InterventionMappingResponse.model_validate(data_dict)
                            elif ev.author == "goal_taxonomist":
                                doc_session.state["goal_mappings"] = GoalMappingResponse.model_validate(data_dict)
                            elif ev.author == "fo_classifier":
                                doc_session.state["functional_areas"] = FunctionalAreaResponse.model_validate(data_dict)
                        except Exception as e:

                            logger.error(f"TAXONOMIST VALIDATION ERROR ({ev.author}): {filename}", error=str(e))
        except Exception as e:
            logger.error(f"ClinicalTaxonomist critical error for: {filename}", error=str(e))
            async with state_lock:
                progress_state.completed += 1
                progress_state.failed += 1
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title if metadata else filename, justification="Taxonomy mapping failed.")

        diag_mappings = doc_session.state.get("diagnosis_mappings")
        int_mappings = doc_session.state.get("intervention_mappings")
        goal_mappings = doc_session.state.get("goal_mappings")
        functional_areas = doc_session.state.get("functional_areas")

        if not functional_areas:
            async with state_lock:
                progress_state.completed += 1
                progress_state.failed += 1
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title, justification="FO classification failed.")

        processed_findings, tax_errors = validate_taxonomy(finding_map, diag_mappings, int_mappings, goal_mappings, functional_areas, doc_id_val, filename, progress_state, state_lock)
        if tax_errors > 0:
            async with state_lock:
                progress_state.total_taxonomy_errors += tax_errors

        async with state_lock:
            progress_state.completed += 1
            progress_state.success += 1
        await progress_queue.put(f"DONE: {filename} (SUCCESS: {len(processed_findings)} findings)")

        res = ProcessedDocument(source_document=metadata.source_document, mapped_findings=processed_findings)
        del doc_session
        gc.collect()
        return res

    except Exception as doc_e:
        if 'cache_path' in locals() and os.path.exists(cache_path):
            os.remove(cache_path)
        async with state_lock:
            progress_state.completed += 1
            progress_state.failed += 1
        logger.error(f"CRITICAL DOCUMENT ERROR: {filename}", error=str(doc_e))
        return ExcludedDocument(source_uri=uri, title=filename, justification="Critical processing error.")
