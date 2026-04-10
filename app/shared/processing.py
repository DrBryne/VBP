"""
Core processing logic for the VBP Workflow.
Handles document preparation, sentence indexing, LLM response parsing, 
and deterministic taxonomy validation.
"""
import asyncio
import re
import nltk
import mimetypes
import fitz  # PyMuPDF
import json
import uuid
import gc
import os
from typing import List, Dict, Tuple, Optional, Any, Union
from google.cloud import storage
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.adk.agents.invocation_context import InvocationContext
from google.genai import types
from app.shared.models import (
    ClinicalFinding, 
    ProcessedFinding, 
    MappedTerm, 
    Document, 
    WorkflowProgress,
    ProcessedDocument,
    ExcludedDocument,
    MetadataResponse,
    ClinicalFindingsResponse,
    IcnpMappingResponse,
    FunctionalAreaResponse
)
from app.shared.taxonomy import load_valid_icnp_ids, is_valid_fo, get_default_fo
from app.shared.tools import parse_gcs_uri
import logging
from app.shared.logging import VBPLogger
from bs4 import BeautifulSoup

logger = VBPLogger("vbp_processing")

# List of supported MIME types
ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/xml", "application/xml"}

# Download NLTK data
try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')

def index_document_sentences(text: str) -> Dict[str, str]:
    """
    Splits document text into individual sentences and assigns unique IDs.
    
    This indexing is the foundation of our 'Read & Point' architecture, 
    eliminating LLM quote hallucinations by resolving evidence via IDs.

    Args:
        text: The raw document text extracted from PDF/XML/TXT.

    Returns:
        A dictionary mapping IDs (S1, S2, ...) to raw sentence strings.
    """
    # Clean up excessive whitespace but preserve basic structure
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = nltk.sent_tokenize(text)
    return {f"S{i+1}": sent for i, sent in enumerate(sentences)}

def format_indexed_text(indexed_sentences: Dict[str, str]) -> str:
    """
    Reconstructs the document with visible sentence IDs for LLM consumption.

    Args:
        indexed_sentences: Dictionary mapping IDs to text.

    Returns:
        A single string where each sentence is prefixed by its ID, e.g., '[S1] Text...'
    """
    parts = []
    for sid, text in indexed_sentences.items():
        parts.append(f"[{sid}] {text}")
    return " ".join(parts)

def _get_cache_dir() -> str:
    """Determines the correct temporary directory for disk-backed caching."""
    # Use /tmp for Agent Engine, or a local .adk/cache for local dev
    if os.environ.get("AGENT_ENGINE_ID"):
        cache_dir = "/tmp/vbp_indexes"
    else:
        cache_dir = ".adk/cache/indexes"
    
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def strip_xml_tags(text: str) -> str:
    """Extracts pure text from XML/HTML strings, replacing tags with spaces."""
    if not text:
        return ""
    try:
        # Use lxml-xml parser for speed and correctness with XML content
        soup = BeautifulSoup(text, "lxml-xml")
        # separator=' ' ensures words in adjacent tags don't run together
        return soup.get_text(separator=' ', strip=True)
    except Exception as e:
        logger.error(f"Error stripping XML tags: {e}")
        return text # Fallback

def safe_parse_json(event: Event) -> Optional[Dict[str, Any]]:
    """
    Safely extracts and parses JSON from an ADK Event.
    
    Uses strict guards for content and parts to prevent AttributeError 
    if an agent returns an empty or safety-blocked response.

    Args:
        event: The final response event from an ADK Agent.

    Returns:
        The parsed dictionary or None if parsing/validation fails.
    """
    if not event.content or not event.content.parts or not event.content.parts[0].text:
        return None
    try:
        return json.loads(event.content.parts[0].text.strip())
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Failed to parse LLM response: {e}")
        return None

def load_and_prep_document(uri: str, project_id: str) -> Tuple[str, str, str]:
    """
    Downloads and cleans document text based on its file format.

    Args:
        uri: The GCS path to the document.
        project_id: The GCP Project ID for storage access.

    Returns:
        A tuple of (filename, mime_type, cleaned_text).
    """
    filename = uri.split("/")[-1]
    mime_type, _ = mimetypes.guess_type(uri)
    
    bucket_name, blob_name = parse_gcs_uri(uri)
    storage_client = storage.Client(project=project_id)
    blob = storage_client.bucket(bucket_name).blob(blob_name)
    
    if mime_type == "application/pdf":
        pdf_bytes = blob.download_as_bytes()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        file_text = "".join([page.get_text() for page in doc])
        doc.close()
    else:
        file_text = blob.download_as_bytes().decode('utf-8', errors='replace')
        if mime_type in ["text/xml", "application/xml"]:
            file_text = strip_xml_tags(file_text)
            
    return filename, mime_type, file_text

async def resolve_sentence_ids(
    finding_candidates: List[ClinicalFinding], 
    doc_id: str, 
    filename: str, 
    progress_state: WorkflowProgress, 
    state_lock: asyncio.Lock, 
    progress_queue: asyncio.Queue
) -> List[ClinicalFinding]:
    """
    Resolves citation IDs back into verbatim text with a surrounding context window.
    
    To improve clinical readability and validation accuracy, this function fetches 
    the cited sentence plus one sentence immediately before and after.

    Args:
        finding_candidates: List of findings with 'supporting_sentence_ids'.
        doc_id: Unique ID used to retrieve the sentence index from disk.
        filename: Name of the document (for logging).
        progress_state: Shared progress tracker.
        state_lock: Async lock for thread-safe counter updates.
        progress_queue: Queue for real-time user events.

    Returns:
        List of findings with resolved 'quotes' text.
    """
    cache_dir = _get_cache_dir()
    cache_path = os.path.join(cache_dir, f"{doc_id}_index.json")
    
    if not os.path.exists(cache_path):
        logger.error(f"[Indexing] Cache missing for {filename} at {cache_path}")
        return []

    with open(cache_path, "r", encoding="utf-8") as f:
        indexed_sentences = json.load(f)

    verified_findings = []
    
    logger.debug(f"Resolving {len(finding_candidates)} findings for {filename} with context window (Disk-Backed)")
    for finding in finding_candidates:
        # Determine the set of unique sentence IDs to include (original + padding)
        # We sort them to ensure narrative order
        unique_ids = set()
        for sid in finding.supporting_sentence_ids:
            try:
                # Extract the numeric index from "S12" -> 12
                idx = int(sid[1:])
                # Add window: idx-1, idx, idx+1
                for offset in [-1, 0, 1]:
                    target_id = f"S{idx + offset}"
                    if target_id in indexed_sentences:
                        unique_ids.add(target_id)
                    elif offset == 0:
                        # Only count the primary ID as hallucinated if missing
                        async with state_lock:
                            progress_state.hallucinated_citations += 1
                        logger.warning(f"[Indexing] Hallucinated Sentence ID '{sid}' in {filename}")
            except (ValueError, IndexError):
                async with state_lock:
                    progress_state.hallucinated_citations += 1
                logger.warning(f"[Indexing] Malformed Sentence ID '{sid}' in {filename}")

        if unique_ids:
            # Sort IDs numerically: S1, S2, S10, S11 (standard string sort fails here)
            sorted_ids = sorted(list(unique_ids), key=lambda x: int(x[1:]))
            
            # Retrieve the raw text without tags and join with spaces
            contextual_quote = " ".join([indexed_sentences[sid] for sid in sorted_ids])
            
            logger.debug(f"Resolved contextual quote ({len(sorted_ids)} sentences) for finding: {finding.nursing_diagnosis}", filename=filename)
            # Store as a single-element list for backward compatibility with downstream models
            finding.quotes = [contextual_quote]
            verified_findings.append(finding)
        else:
            async with state_lock:
                progress_state.dropped_findings += 1
            await progress_queue.put(f"VALIDATION: Dropped finding with no valid sentence IDs in {filename}")
            logger.warning(f"[Indexing] Dropping finding in {filename} (no valid IDs remain): {finding.nursing_diagnosis}")
            
    # Cleanup memory
    del indexed_sentences
    return verified_findings

async def process_document_pipeline(
    uri: str,
    target_group: str,
    project_id: str,
    research_analyst: BaseAgent,
    term_mapper: BaseAgent,
    parent_ctx: InvocationContext,
    ephemeral_session_service: InMemorySessionService,
    progress_state: WorkflowProgress,
    state_lock: asyncio.Lock,
    progress_queue: asyncio.Queue
) -> Union[ProcessedDocument, ExcludedDocument]:
    """
    Executes the full extraction and mapping pipeline for a single document.
    
    This is an encapsulated 'Sub-Workflow' that handles the transition from 
    unstructured text to validated, mapped clinical findings.

    Args:
        uri: Document GCS location.
        target_group: The clinical scope (e.g., 'ALS').
        project_id: GCP project.
        research_analyst: Agent for finding extraction.
        term_mapper: Agent for terminology mapping.
        parent_ctx: InvocationContext for trace inheritance.
        ephemeral_session_service: Service for isolated doc sessions.
        progress_state: Shared progress dataclass.
        state_lock: Concurrency lock.
        progress_queue: User event queue.

    Returns:
        A ProcessedDocument on success, or an ExcludedDocument on failure.
    """
    doc_id = str(uuid.uuid4())
    try:
        filename = uri.split("/")[-1]
        logger.info(f"Processing document start: {filename}", uri=uri)
        await progress_queue.put(f"START: {filename}")
        
        # 1. Load and Prep
        filename, mime_type, file_text = load_and_prep_document(uri, project_id)
        
        if mime_type not in ALLOWED_MIME_TYPES:
            async with state_lock: progress_state.completed += 1; progress_state.failed += 1
            logger.warning(f"Unsupported file type: {mime_type}", filename=filename, uri=uri)
            await progress_queue.put(f"DONE: {filename} (UNSUPPORTED TYPE: {mime_type})")
            return ExcludedDocument(
                source_uri=uri,
                title=filename,
                justification=f"The document was excluded because its file type ({mime_type}) is not supported for analysis. Only PDF, TXT, and XML are allowed."
            )

        # 2. Index Sentences & Persist to Disk
        logger.debug(f"Indexing document sentences: {filename}")
        indexed_sentences = index_document_sentences(file_text)
        tagged_text = format_indexed_text(indexed_sentences)
        
        # PERSIST TO DISK TO SAVE RAM
        cache_dir = _get_cache_dir()
        cache_path = os.path.join(cache_dir, f"{doc_id}_index.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(indexed_sentences, f)
        
        logger.debug(f"Document indexed and cached to disk: {filename}", sentence_count=len(indexed_sentences))
        
        # Aggressive memory cleanup
        del file_text
        del indexed_sentences

        # 3. Setup Session
        doc_session_id = str(uuid.uuid4())
        doc_session = await ephemeral_session_service.create_session(app_name="vbp_workflow", user_id="system", session_id=doc_session_id)
        
        # 4. Invoke Research Analyst
        static_context = types.Part.from_text(text=f"Target Group: {target_group}\n\nAnalyze the attached document.")
        analyst_msg = types.Content(role="user", parts=[static_context, types.Part.from_text(text=f"Document Content (with Sentence IDs):\n{tagged_text}")])
        doc_session.events.append(Event(author="system", content=analyst_msg))
        
        # Memory cleanup
        del tagged_text
        
        # Inherit from parent context but update session and invocation ID
        pipeline_ctx = parent_ctx.model_copy(update={
            "session": doc_session,
            "invocation_id": str(uuid.uuid4()),
            "session_service": ephemeral_session_service
        })

        logger.info(f"Invoking Research Analyst for: {filename}")
        async for ev in research_analyst.run_async(pipeline_ctx):
            if ev.is_final_response():
                data_dict = safe_parse_json(ev)
                if not data_dict:
                    continue
                try:
                    if ev.author == "metadata_extractor": 
                        doc_session.state["metadata"] = MetadataResponse.model_validate(data_dict)
                        logger.debug(f"Metadata extracted for: {filename}", title=data_dict.get("source_document", {}).get("title"))
                    elif ev.author == "finding_extractor": 
                        doc_session.state["clinical_findings"] = ClinicalFindingsResponse.model_validate(data_dict)
                        logger.debug(f"Findings extracted for: {filename}", count=len(data_dict.get("candidate_findings", [])))
                except Exception as e: 
                    logger.error(f"ANALYST VALIDATION ERROR ({ev.author}): {filename}", error=str(e))
                    await progress_queue.put(f"ANALYST VALIDATION ERROR ({ev.author}): {filename} ({e})")

        metadata: MetadataResponse = doc_session.state.get("metadata")
        clinical_findings: ClinicalFindingsResponse = doc_session.state.get("clinical_findings")
        
        if not metadata:
            metadata = MetadataResponse(source_document=Document(source_uri=uri, title=filename, publication_year=0, doi="Not found", evidence_level="Nivå 0: Ingen kategori"))
        else:
            metadata.source_document.source_uri = uri

        if not clinical_findings or not clinical_findings.candidate_findings:
            async with state_lock: progress_state.completed += 1; progress_state.no_findings += 1
            logger.info(f"No findings identified in document: {filename}")
            await progress_queue.put(f"DONE: {filename} (NO FINDINGS)")
            # Cleanup cache
            if os.path.exists(cache_path): os.remove(cache_path)
            return ExcludedDocument(
                source_uri=uri, 
                title=metadata.source_document.title, 
                justification=clinical_findings.reasoning_trace if clinical_findings else "Ingen kliniske funn identifisert."
            )

        # 5. Resolve Sentence IDs (Loads from Disk)
        logger.debug(f"Resolving sentence IDs for: {filename}")
        verified_findings = await resolve_sentence_ids(
            clinical_findings.candidate_findings, 
            doc_id, 
            filename, 
            progress_state, 
            state_lock, 
            progress_queue
        )
        
        # Cleanup cache after resolution
        if os.path.exists(cache_path): os.remove(cache_path)

        if not verified_findings:
            async with state_lock: progress_state.completed += 1; progress_state.no_findings += 1
            logger.warning(f"No valid citations remain after resolution for: {filename}")
            await progress_queue.put(f"DONE: {filename} (NO VALID CITATIONS)")
            return ExcludedDocument(
                source_uri=uri, 
                title=metadata.source_document.title, 
                justification="The document was analyzed, but all identified clinical findings were excluded because the associated sentence citations were invalid."
            )
        
        clinical_findings.candidate_findings = verified_findings
        doc_id_val = metadata.source_document.document_id or str(uuid.uuid4())
        metadata.source_document.document_id = doc_id_val
        metadata.source_document.reasoning_trace = clinical_findings.reasoning_trace

        # 6. Invoke Term Mapper
        lean_findings = []
        finding_map = {}
        for finding in clinical_findings.candidate_findings:
            internal_id = str(uuid.uuid4()); finding_map[internal_id] = finding
            lean_findings.append({"finding_id": internal_id, "nursing_diagnosis": finding.nursing_diagnosis, "intervention": finding.intervention, "goal": finding.goal})

        mapper_msg = types.Content(role="user", parts=[types.Part.from_text(text="Map these findings to ICNP and classify FO:"), types.Part.from_text(text=json.dumps(lean_findings))])
        doc_session.events.append(Event(author="system", content=mapper_msg))
        
        logger.info(f"Invoking Term Mapper for: {filename}", finding_count=len(lean_findings))
        try:
            async for ev in term_mapper.run_async(pipeline_ctx):
                if ev.is_final_response():
                    data_dict = safe_parse_json(ev)
                    if not data_dict:
                        continue
                    try:
                        if ev.author == "icnp_mapper": doc_session.state["icnp_mappings"] = IcnpMappingResponse.model_validate(data_dict)
                        elif ev.author == "fo_classifier": doc_session.state["functional_areas"] = FunctionalAreaResponse.model_validate(data_dict)
                    except Exception as e: 
                        logger.error(f"MAPPER VALIDATION ERROR ({ev.author}): {filename}", error=str(e))
                        await progress_queue.put(f"MAPPER VALIDATION ERROR ({ev.author}): {filename} ({e})")
        except Exception as e:
            async with state_lock: progress_state.completed += 1; progress_state.failed += 1
            logger.error(f"Term Mapper critical error for: {filename}", error=str(e))
            await progress_queue.put(f"DONE: {filename} (MAPPER ERROR: {e})")
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title, justification="The document content was unexpected or incompatible with the standardized clinical mapping terminology.")
            
        icnp_mappings: IcnpMappingResponse = doc_session.state.get("icnp_mappings")
        functional_areas: FunctionalAreaResponse = doc_session.state.get("functional_areas")
        if not functional_areas:
            async with state_lock: progress_state.completed += 1; progress_state.failed += 1
            logger.error(f"Mapper incomplete for: {filename}")
            await progress_queue.put(f"DONE: {filename} (MAPPER INCOMPLETE)")
            return ExcludedDocument(source_uri=uri, title=metadata.source_document.title, justification="The document content was unexpected or incompatible with the standardized clinical mapping terminology.")
            
        icnp_lookup = {res.finding_id: res for res in icnp_mappings.results} if icnp_mappings else {}
        fo_lookup = {res.finding_id: res.FO for res in functional_areas.results}
        
        # 7. Validate Taxonomy
        logger.debug(f"Validating taxonomy for: {filename}")
        processed_findings, taxonomy_error_count = validate_taxonomy(finding_map, icnp_lookup, fo_lookup, doc_id_val, filename, progress_state, state_lock)
        if taxonomy_error_count > 0:
            async with state_lock: progress_state.total_taxonomy_errors += taxonomy_error_count

        async with state_lock: progress_state.completed += 1; progress_state.success += 1
        logger.info(f"Document processing success: {filename}", finding_count=len(processed_findings))
        await progress_queue.put(f"DONE: {filename} (SUCCESS: {len(processed_findings)} findings)")
        
        # Final cleanup for this document task
        res = ProcessedDocument(source_document=metadata.source_document, mapped_findings=processed_findings)
        del doc_session
        gc.collect() # Force GC reclaim
        return res

    except Exception as doc_e:
        # Final safety cleanup
        if 'cache_path' in locals() and os.path.exists(cache_path): os.remove(cache_path)
        async with state_lock: progress_state.completed += 1; progress_state.failed += 1
        logger.error(f"CRITICAL DOCUMENT ERROR: {filename}", error=str(doc_e), uri=uri)
        err_msg = f"Failed to process document: {filename} (Error: {str(doc_e)})"
        await progress_queue.put(f"WARNING: {err_msg}")
        return ExcludedDocument(source_uri=uri, title=filename, justification="The document could not be read or its format was unsupported for analysis.")

def validate_taxonomy(
    finding_map: Dict[str, ClinicalFinding], 
    icnp_lookup: Dict, 
    fo_lookup: Dict, 
    doc_id: str,
    filename: str, 
    progress_state: WorkflowProgress, 
    state_lock: asyncio.Lock
) -> Tuple[List[ProcessedFinding], int]:
    """
    Cross-references LLM mapping results against the master ICNP dictionary.
    
    This deterministic step ensures that even if an LLM hallucinates a 
    terminology code, it is cleared before reaching the final report.

    Args:
        finding_map: Original clinical findings.
        icnp_lookup: Terminology matches from TermMapper.
        fo_lookup: Functional Area assignments.
        doc_id: Source document ID.
        filename: Source document name.
        progress_state: Progress tracking dataclass.
        state_lock: Concurrency lock.

    Returns:
        Tuple of (List of validated findings, error count).
    """
    valid_icnp_ids = load_valid_icnp_ids()
    processed_findings = []
    taxonomy_error_count = 0
    
    logger.debug(f"Validating taxonomy for {len(finding_map)} findings in {filename}")
    for f_id, original in finding_map.items():
        icnp_match = icnp_lookup.get(f_id)
        fo_val = fo_lookup.get(f_id, get_default_fo())
        
        # FO is now validated by Pydantic Enum in the mapper, so we don't need manual check here
        # but we use get_default_fo() as a safe fallback for the dict lookup above.

        def resolve(orig_val, mapping_field, field_name):
            nonlocal taxonomy_error_count
            if mapping_field and mapping_field.term:
                concept_id = mapping_field.ICNP_concept_id
                if concept_id and concept_id not in valid_icnp_ids:
                    taxonomy_error_count += 1
                    logger.warning(f"[Taxonomy Validation] Hallucinated ICNP ID '{concept_id}' removed in {filename}.", field=field_name, finding_id=f_id)
                    concept_id = ""
                return MappedTerm(term=mapping_field.term, ICNP_concept_id=concept_id)
            return MappedTerm(term=orig_val, ICNP_concept_id="")
        
        processed_findings.append(ProcessedFinding(
            finding_id=f_id, 
            document_id=doc_id, 
            nursing_diagnosis=original.nursing_diagnosis,
            intervention=original.intervention, 
            goal=original.goal, 
            supporting_sentence_ids=original.supporting_sentence_ids,
            quotes=original.quotes,
            mapped_nursing_diagnosis=resolve(original.nursing_diagnosis, icnp_match.nursing_diagnosis if icnp_match else None, "nursing_diagnosis"),
            mapped_intervention=resolve(original.intervention, icnp_match.intervention if icnp_match else None, "intervention"),
            mapped_goal=resolve(original.goal, icnp_match.goal if icnp_match else None, "goal"),
            FO=fo_val
        ))
    
    if taxonomy_error_count > 0:
        logger.info(f"Taxonomy validation complete for {filename}: {taxonomy_error_count} errors corrected.", filename=filename)
        
    return processed_findings, taxonomy_error_count
