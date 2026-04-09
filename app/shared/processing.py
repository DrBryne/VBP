import asyncio
import re
import nltk
from typing import List, Dict, Tuple, Optional, Any
from app.shared.models import ClinicalFinding, ProcessedFinding, MappedTerm, Document, WorkflowProgress
from app.shared.taxonomy import load_valid_icnp_ids, is_valid_fo, get_default_fo
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger("vbp_processing")

# Download NLTK data
try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')

def index_document_sentences(text: str) -> Dict[str, str]:
    """Splits text into sentences and assigns unique IDs (S1, S2, ...)."""
    # Clean up excessive whitespace but preserve basic structure
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = nltk.sent_tokenize(text)
    return {f"S{i+1}": sent for i, sent in enumerate(sentences)}

def format_indexed_text(indexed_sentences: Dict[str, str]) -> str:
    """Reconstructs the document with visible sentence IDs for the LLM."""
    parts = []
    for sid, text in indexed_sentences.items():
        parts.append(f"[{sid}] {text}")
    return " ".join(parts)

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

async def resolve_sentence_ids(
    finding_candidates: List[ClinicalFinding], 
    indexed_sentences: Dict[str, str],
    filename: str, 
    progress_state: WorkflowProgress, 
    state_lock: asyncio.Lock, 
    progress_queue: asyncio.Queue
) -> List[ClinicalFinding]:
    """
    Resolves supporting_sentence_ids back into actual text quotes.
    Drops hallucinated IDs and findings with no valid quotes.
    """
    verified_findings = []
    
    for finding in finding_candidates:
        valid_quotes = []
        # Finding candidates currently has supporting_sentence_ids from the LLM
        for sid in finding.supporting_sentence_ids:
            quote_text = indexed_sentences.get(sid)
            if quote_text:
                valid_quotes.append(quote_text)
            else:
                logger.warning(f"[Indexing] Hallucinated Sentence ID '{sid}' in {filename}")
        
        if valid_quotes:
            # We dynamically add 'quotes' attribute so the rest of the workflow
            # (Taxonomy, Consolidation) can use it without schema changes.
            finding.quotes = valid_quotes
            verified_findings.append(finding)
        else:
            async with state_lock:
                progress_state.dropped_findings += 1
            await progress_queue.put(f"VALIDATION: Dropped finding with no valid sentence IDs in {filename}")
            logger.warning(f"[Indexing] Dropping finding in {filename} (no valid IDs remain): {finding.nursing_diagnosis}")
            
    return verified_findings

def validate_taxonomy(
    finding_map: Dict[str, ClinicalFinding], 
    icnp_lookup: Dict, 
    fo_lookup: Dict, 
    doc_id: str,
    filename: str, 
    progress_state: WorkflowProgress, 
    state_lock: asyncio.Lock
) -> List[ProcessedFinding]:
    """
    Cross-references findings with the ICNP taxonomy and Functional Areas (FO).
    Handles hallucinated IDs and defaults invalid FO categories.
    """
    valid_icnp_ids = load_valid_icnp_ids()
    processed_findings = []
    taxonomy_error_count = 0
    
    for f_id, original in finding_map.items():
        icnp_match = icnp_lookup.get(f_id)
        fo_val = fo_lookup.get(f_id, get_default_fo())
        
        # Validate FO
        if not is_valid_fo(fo_val):
            taxonomy_error_count += 1
            logger.warning(f"[Taxonomy Validation] Invalid FO '{fo_val}' in {filename}, defaulting.")
            fo_val = get_default_fo()

        def resolve(orig_val, mapping_field):
            nonlocal taxonomy_error_count
            if mapping_field and mapping_field.term:
                concept_id = mapping_field.ICNP_concept_id
                if concept_id and concept_id not in valid_icnp_ids:
                    taxonomy_error_count += 1
                    logger.warning(f"[Taxonomy Validation] Hallucinated ICNP ID '{concept_id}' removed in {filename}.")
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
            mapped_nursing_diagnosis=resolve(original.nursing_diagnosis, icnp_match.nursing_diagnosis if icnp_match else None),
            mapped_intervention=resolve(original.intervention, icnp_match.intervention if icnp_match else None),
            mapped_goal=resolve(original.goal, icnp_match.goal if icnp_match else None),
            FO=fo_val
        ))
    
    if taxonomy_error_count > 0:
        # We handle the lock externally if needed, or internally here.
        # Given this is synchronous and called within an async task, we use a loop-level update.
        pass # The orchestrator will add this to progress_state after calling.
        
    return processed_findings, taxonomy_error_count
