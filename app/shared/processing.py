import asyncio
import re
from typing import List, Dict, Tuple, Optional, Any
from rapidfuzz import fuzz
from app.shared.models import ClinicalFinding, ProcessedFinding, MappedTerm, Document, WorkflowProgress
from app.shared.taxonomy import load_valid_icnp_ids, is_valid_fo, get_default_fo
import logging

logger = logging.getLogger("vbp_processing")

def normalize_text(text: str) -> str:
    """Removes all non-alphanumeric characters for robust fuzzy matching."""
    return re.sub(r'[\W_]+', '', text.lower())

async def verify_quotes_fuzzy(
    finding_candidates: List[ClinicalFinding], 
    source_text: str, 
    filename: str, 
    progress_state: WorkflowProgress, 
    state_lock: asyncio.Lock, 
    progress_queue: asyncio.Queue
) -> Tuple[List[ClinicalFinding], int]:
    """
    Verifies and rectifies clinical quotes using exact and fuzzy matching.
    Drops hallucinated quotes and findings with no valid quotes.
    """
    verified_findings = []
    rectified_count = 0
    
    for finding in finding_candidates:
        valid_quotes = []
        for quote in finding.quotes:
            # 1. Exact match fallback (case-insensitive)
            quote_clean = quote.strip()
            if quote_clean.lower() in source_text.lower():
                valid_quotes.append(quote_clean)
                continue
            
            # 2. Fuzzy alignment
            alignment = fuzz.partial_ratio_alignment(quote_clean.lower(), source_text.lower(), score_cutoff=85.0)
            
            if alignment and alignment.score >= 90.0:
                verbatim_text = source_text[alignment.src_start:alignment.src_end]
                valid_quotes.append(verbatim_text)
                rectified_count += 1
                async with state_lock:
                    progress_state.rectified_quotes += 1
                logger.info(f"[Quote Rectification] Fixed minor mismatch in {filename} (Score: {alignment.score:.1f})")
            else:
                await progress_queue.put(f"VALIDATION: Dropped hallucinated quote from {filename}")
                logger.warning(f"[Quote Verification] Hallucinated quote dropped in {filename}: {quote[:50]}...")
        
        if valid_quotes:
            finding.quotes = valid_quotes
            verified_findings.append(finding)
        else:
            async with state_lock:
                progress_state.dropped_findings += 1
            await progress_queue.put(f"VALIDATION: Dropped finding with no valid quotes in {filename}")
            logger.warning(f"[Quote Verification] Dropping finding in {filename} (no valid quotes remain): {finding.nursing_diagnosis}")
            
    return verified_findings, rectified_count

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
