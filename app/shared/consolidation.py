"""
Consolidation and Synthesis logic for the VBP Workflow.
Handles the grouping of findings across documents and the assembly
of the final clinical report.
"""
from typing import List, Dict, Any
from datetime import datetime

from app.shared.models import (
    Document,
    Evidence,
    ExcludedDocument,
    ExecutionSummary,
    ProcessedDocument,
    SynthesisResponse,
    SynthesizedFinding,
)


def group_findings(processed_docs: List[ProcessedDocument]) -> Dict[str, Dict]:
    """
    Groups individual findings by Functional Area (FO) and ICNP Concept ID.

    This is the core clinical synthesis step. It aggregates findings from
    multiple documents into a single representative finding for the final
    report, preserving all supporting evidence and calculating trust metrics.

    Args:
        processed_docs: List of successfully processed documents with findings.

    Returns:
        A dictionary mapping group keys (FO||ICNP_ID) to aggregated finding data.
    """
    groups = {}
    
    # Evidence Level Mapping (Knowledge Pyramid)
    # Higher score = Higher scientific reliability
    LEVEL_WEIGHTS = {
        "Nivå 1": 10.0, # Studies
        "Nivå 2": 15.0, # Systematic Reviews
        "Nivå 3": 5.0,  # Guidelines
        "Nivå 4": 3.0,  # Manuals
    }

    for doc in processed_docs:
        doc_id = doc.source_document.document_id
        source_level = doc.source_document.evidence_level
        
        # Determine weight based on the Knowledge Pyramid
        doc_weight = 1.0
        for key, weight in LEVEL_WEIGHTS.items():
            if key in source_level:
                doc_weight = weight
                break

        for finding in doc.mapped_findings:
            # Create a unique key based on FO and the mapped diagnosis concept ID
            # Fall back to the term text if no concept ID exists
            icnp_id = finding.mapped_nursing_diagnosis.ICNP_concept_id or finding.mapped_nursing_diagnosis.term
            group_key = f"{finding.FO}||{icnp_id}"

            if group_key not in groups:
                groups[group_key] = {
                    "FO": finding.FO,
                    "nursing_diagnosis": finding.mapped_nursing_diagnosis,
                    "intervention": finding.mapped_intervention, 
                    "goal": finding.mapped_goal, 
                    "supporting_evidence": {}, 
                    "specificity_scores": [],
                    "actionability_scores": [],
                    "cohesion_scores": [],
                    "weighted_sum": 0.0,
                    "consensus_count": 0
                }

            # Aggregate evidence
            if doc_id not in groups[group_key]["supporting_evidence"]:
                groups[group_key]["supporting_evidence"][doc_id] = []

            for quote in finding.quotes:
                if quote not in groups[group_key]["supporting_evidence"][doc_id]:
                    groups[group_key]["supporting_evidence"][doc_id].append(quote)

            # Aggregate quality metrics from the Auditor
            if finding.auditor_rating:
                groups[group_key]["specificity_scores"].append(finding.auditor_rating.specificity_score)
                groups[group_key]["actionability_scores"].append(finding.auditor_rating.actionability_score)
                groups[group_key]["cohesion_scores"].append(finding.auditor_rating.cohesion_score)
            
            # Calculate Trust Contribution: scientific weight of the source
            groups[group_key]["weighted_sum"] += doc_weight
            groups[group_key]["consensus_count"] += 1

    return groups

def finalize_synthesis(
    target_group: str,
    source_uri: str,
    total_files_in_uri: int,
    execution_start_time: datetime,
    execution_end_time: datetime,
    grouped_data: Dict[str, Dict],
    source_documents: List[Document],
    excluded_documents: List[ExcludedDocument],
    total_hallucinated_citations: int = 0,
    total_dropped_findings: int = 0,
    total_taxonomy_errors: int = 0
) -> SynthesisResponse:
    """
    Assembles the final SynthesisResponse object with all clinical and operational data.

    Args:
        target_group: The clinical scope of the analysis.
        source_uri: The GCS path scanned.
        total_files_in_uri: Number of files discovered in GCS.
        execution_start_time: Start timestamp.
        execution_end_time: Finish timestamp.
        grouped_data: Findings aggregated by FO and ICNP ID.
        source_documents: Successful document metadata.
        excluded_documents: Omitted document metadata and justifications.
        total_hallucinated_citations: Count of corrected citation IDs.
        total_dropped_findings: Count of removed unsupported findings.
        total_taxonomy_errors: Count of corrected taxonomy codes.

    Returns:
        A complete SynthesisResponse ready for the user.
    """
    synthesized_findings = []

    for _group_key, data in grouped_data.items():
        # Build the supporting evidence list of Evidence objects
        evidence_list = []
        for doc_id, quotes in data["supporting_evidence"].items():
            evidence_list.append(Evidence(document_id=doc_id, quotes=quotes))

        # Calculate final aggregated metrics
        avg_spec = sum(data["specificity_scores"]) / len(data["specificity_scores"]) if data["specificity_scores"] else 5.0
        avg_act = sum(data["actionability_scores"]) / len(data["actionability_scores"]) if data["actionability_scores"] else 5.0
        avg_coh = sum(data["cohesion_scores"]) / len(data["cohesion_scores"]) if data["cohesion_scores"] else 5.0
        
        # Trust Score = Scientific Weight Sum + (Consensus Bonus)
        # Consensus bonus rewards findings appearing in multiple documents
        consensus_bonus = (data["consensus_count"] - 1) * 2.0
        trust_score = data["weighted_sum"] + max(0, consensus_bonus)

        synthesized_findings.append(SynthesizedFinding(
            nursing_diagnosis=data["nursing_diagnosis"],
            intervention=data["intervention"],
            goal=data["goal"],
            FO=data["FO"],
            avg_specificity=round(avg_spec, 1),
            avg_actionability=round(avg_act, 1),
            avg_cohesion=round(avg_coh, 1),
            trust_score=round(trust_score, 1),
            supporting_evidence=evidence_list
        ))
    
    # SORTING: Primary = TrustScore (Descending), Secondary = Specificity
    synthesized_findings.sort(key=lambda x: (x.trust_score, x.avg_specificity), reverse=True)

    summary = ExecutionSummary(
        target_group=target_group,
        source_uri=source_uri,
        total_files_in_uri=total_files_in_uri,
        processed_files_count=len(source_documents) + len(excluded_documents),
        successful_files_count=len(source_documents),
        excluded_files_count=len(excluded_documents),
        total_synthesized_findings=len(synthesized_findings),
        total_hallucinated_citations=total_hallucinated_citations,
        total_dropped_findings=total_dropped_findings,
        total_taxonomy_errors=total_taxonomy_errors,
        execution_start_time=execution_start_time,
        execution_end_time=execution_end_time
    )

    return SynthesisResponse(
        execution_summary=summary,
        synthesized_findings=synthesized_findings,
        source_documents=source_documents,
        excluded_documents=excluded_documents
    )
