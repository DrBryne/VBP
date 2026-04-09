from typing import List, Dict
from datetime import datetime
from app.shared.models import (
    ProcessedDocument, 
    SynthesizedFinding, 
    Evidence, 
    MappedTerm, 
    Document,
    ExcludedDocument,
    ExecutionSummary,
    SynthesisResponse
)

def group_findings(processed_docs: List[ProcessedDocument]) -> Dict[str, Dict]:
    """
    Groups findings by Functional Area (FO) and ICNP Concept ID.
    Returns a dictionary structure suitable for synthesis.
    """
    groups = {}
    
    for doc in processed_docs:
        doc_id = doc.source_document.document_id
        for finding in doc.mapped_findings:
            # Create a unique key based on FO and the mapped diagnosis concept ID
            # Fall back to the term text if no concept ID exists
            diag_id = finding.mapped_nursing_diagnosis.ICNP_concept_id or finding.mapped_nursing_diagnosis.term
            group_key = f"{finding.FO}||{diag_id}"
            
            if group_key not in groups:
                groups[group_key] = {
                    "FO": finding.FO,
                    "nursing_diagnosis": finding.mapped_nursing_diagnosis,
                    "intervention": finding.mapped_intervention, # Initial representative
                    "goal": finding.mapped_goal, # Initial representative
                    "supporting_evidence": {}, # doc_id -> list of quotes
                    "all_findings": [] # List of raw findings for LLM context
                }
            
            # Aggregate quotes for this specific document in this group
            if doc_id not in groups[group_key]["supporting_evidence"]:
                groups[group_key]["supporting_evidence"][doc_id] = []
            
            # Extend quotes, avoiding duplicates
            for quote in finding.quotes:
                if quote not in groups[group_key]["supporting_evidence"][doc_id]:
                    groups[group_key]["supporting_evidence"][doc_id].append(quote)
            
            groups[group_key]["all_findings"].append(finding)
            
    return groups

def finalize_synthesis(
    target_group: str,
    source_uri: str,
    total_files_in_uri: int,
    execution_start_time: datetime,
    execution_end_time: datetime,
    grouped_data: Dict[str, Dict],
    summaries: Dict[str, str],
    quality_notes: str,
    source_documents: List[Document],
    excluded_documents: List[ExcludedDocument],
    total_rectified_quotes: int = 0,
    total_unsupported_quotes_dropped: int = 0,
    total_dropped_findings: int = 0,
    total_taxonomy_errors: int = 0
) -> SynthesisResponse:
    """
    Assembles the final SynthesisResponse object.
    """
    synthesized_findings = []
    
    for group_key, data in grouped_data.items():
        # Build the supporting evidence list of Evidence objects
        evidence_list = []
        for doc_id, quotes in data["supporting_evidence"].items():
            evidence_list.append(Evidence(document_id=doc_id, quotes=quotes))
            
        synthesized_findings.append(SynthesizedFinding(
            nursing_diagnosis=data["nursing_diagnosis"],
            intervention=data["intervention"],
            goal=data["goal"],
            FO=data["FO"],
            evidence_summary=summaries.get(group_key, "Ingen oppsummering tilgjengelig."),
            supporting_evidence=evidence_list
        ))
        
    summary = ExecutionSummary(
        target_group=target_group,
        source_uri=source_uri,
        total_files_in_uri=total_files_in_uri,
        processed_files_count=len(source_documents) + len(excluded_documents),
        successful_files_count=len(source_documents),
        excluded_files_count=len(excluded_documents),
        total_synthesized_findings=len(synthesized_findings),
        total_rectified_quotes=total_rectified_quotes,
        total_unsupported_quotes_dropped=total_unsupported_quotes_dropped,
        total_dropped_findings=total_dropped_findings,
        total_taxonomy_errors=total_taxonomy_errors,
        execution_start_time=execution_start_time,
        execution_end_time=execution_end_time,
        quality_notes=quality_notes
    )
        
    return SynthesisResponse(
        execution_summary=summary,
        synthesized_findings=synthesized_findings,
        source_documents=source_documents,
        excluded_documents=excluded_documents
    )
