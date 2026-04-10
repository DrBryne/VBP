"""
Consolidation and Synthesis logic for the VBP Workflow.
Handles the grouping of findings across documents and the assembly
of the final clinical report.
"""
import asyncio
from datetime import datetime

from app.shared.fhir_client import FhirTerminologyClient
from app.shared.logging import VBPLogger
from app.shared.models import (
    Document,
    Evidence,
    ExcludedDocument,
    ExecutionSummary,
    ProcessedDocument,
    SynthesisResponse,
    SynthesizedFinding,
)

logger = VBPLogger("vbp_consolidation")

async def audit_semantic_relationships(unique_ids: set[str], fhir_client: FhirTerminologyClient) -> dict[str, str]:
    """
    Queries the FHIR server to find hierarchical relationships between a set of ICNP IDs.
    Returns a mapping of {child_id: parent_id} for concepts that should be merged.
    If an ID has no parent in the set, it maps to itself.
    """
    id_list = list(unique_ids)
    rewrite_map = {cid: cid for cid in id_list}

    if len(id_list) < 2:
        return rewrite_map

    # Create all possible pairs (A, B) to check if A is subsumed by B
    tasks = []
    pairs = []
    for i in range(len(id_list)):
        for j in range(len(id_list)):
            if i != j:
                tasks.append(fhir_client.check_subsumption(id_list[i], id_list[j]))
                pairs.append((id_list[i], id_list[j]))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for (child, parent), result in zip(pairs, results, strict=False):
        if isinstance(result, Exception):
            logger.error(f"FHIR Audit Error: {result}", child=child, parent=parent)
            continue

        if result == "subsumed-by":
            logger.info(f"[Semantic Merge] '{child}' is a sub-concept of '{parent}'. Merging.")
            rewrite_map[child] = parent

    return rewrite_map

async def group_findings(processed_docs: list[ProcessedDocument], fhir_client: FhirTerminologyClient) -> dict[str, dict]:
    """
    Groups individual findings by Functional Area (FO) and ICNP Concept ID.

    This is the core clinical synthesis step. It aggregates findings from
    multiple documents into a single representative finding for the final
    report, preserving all supporting evidence and calculating trust metrics.
    
    Uses FHIR terminology to deterministically merge sub-concepts into their parents.

    Args:
        processed_docs: List of successfully processed documents with findings.
        fhir_client: The initialized FHIR terminology client.

    Returns:
        A dictionary mapping group keys (FO||ICNP_ID) to aggregated finding data.
    """
    groups = {}

    # Evidence Level Mapping (Knowledge Pyramid)
    LEVEL_WEIGHTS = {
        "Nivå 1": 10.0,
        "Nivå 2": 15.0,
        "Nivå 3": 5.0,
        "Nivå 4": 3.0,
    }

    # Phase 1: Discovery - Collect all unique ICNP IDs per Functional Area
    fo_id_map: dict[str, set[str]] = {}
    for doc in processed_docs:
        for finding in doc.mapped_findings:
            fo = finding.FO
            icnp_id = finding.mapped_nursing_diagnosis.ICNP_concept_id
            if icnp_id:
                if fo not in fo_id_map:
                    fo_id_map[fo] = set()
                fo_id_map[fo].add(icnp_id)

    # Phase 2: Semantic Audit - Determine which IDs should be merged
    global_rewrite_map = {}
    audit_tasks = []
    fo_keys = list(fo_id_map.keys())

    for fo in fo_keys:
        audit_tasks.append(audit_semantic_relationships(fo_id_map[fo], fhir_client))

    if fo_keys:
        logger.info(f"Starting FHIR semantic audit for {len(fo_keys)} Functional Areas.")
        audit_results = await asyncio.gather(*audit_tasks)

        for fo, rewrite_map in zip(fo_keys, audit_results, strict=False):
            for child, parent in rewrite_map.items():
                # Create a global mapping: FO||Child -> FO||Parent
                global_rewrite_map[f"{fo}||{child}"] = f"{fo}||{parent}"

    # Phase 3: Smart Merging & Aggregation
    for doc in processed_docs:
        doc_id = doc.source_document.document_id
        source_level = doc.source_document.evidence_level

        doc_weight = 1.0
        for key, weight in LEVEL_WEIGHTS.items():
            if key in source_level:
                doc_weight = weight
                break

        for finding in doc.mapped_findings:
            raw_icnp_id = finding.mapped_nursing_diagnosis.ICNP_concept_id
            base_key = f"{finding.FO}||{raw_icnp_id}" if raw_icnp_id else f"{finding.FO}||{finding.mapped_nursing_diagnosis.term}"

            # Apply the Semantic Rewrite Map
            group_key = global_rewrite_map.get(base_key, base_key)

            if group_key not in groups:
                groups[group_key] = {
                    "FO": finding.FO,
                    "nursing_diagnosis": finding.mapped_nursing_diagnosis,
                    "interventions": [],
                    "goals": [],
                    "supporting_evidence": {},
                    "specificity_scores": [],
                    "actionability_scores": [],
                    "cohesion_scores": [],
                    "weighted_sum": 0.0,
                    "consensus_count": 0
                }

            # Add unique interventions
            if finding.mapped_intervention not in groups[group_key]["interventions"]:
                groups[group_key]["interventions"].append(finding.mapped_intervention)

            # Add unique goals
            if finding.mapped_goal not in groups[group_key]["goals"]:
                groups[group_key]["goals"].append(finding.mapped_goal)

            # Aggregate evidence with GRADE metadata
            if doc_id not in groups[group_key]["supporting_evidence"]:
                groups[group_key]["supporting_evidence"][doc_id] = {
                    "quotes": [],
                    "evidence_grade": finding.evidence_grade,
                    "recommendation_strength": finding.recommendation_strength,
                    "grade_quotes": finding.grade_quotes
                }

            for quote in finding.quotes:
                if quote not in groups[group_key]["supporting_evidence"][doc_id]["quotes"]:
                    groups[group_key]["supporting_evidence"][doc_id]["quotes"].append(quote)

            # Aggregate quality metrics from the Auditor
            if finding.auditor_rating:
                groups[group_key]["specificity_scores"].append(finding.auditor_rating.specificity_score)
                groups[group_key]["actionability_scores"].append(finding.auditor_rating.actionability_score)
                groups[group_key]["cohesion_scores"].append(finding.auditor_rating.cohesion_score)

            groups[group_key]["weighted_sum"] += doc_weight
            groups[group_key]["consensus_count"] += 1

    return groups

def finalize_synthesis(
    target_group: str,
    source_uri: str,
    total_files_in_uri: int,
    execution_start_time: datetime,
    execution_end_time: datetime,
    grouped_data: dict[str, dict],
    source_documents: list[Document],
    excluded_documents: list[ExcludedDocument],
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
        for doc_id, ev_data in data["supporting_evidence"].items():
            evidence_list.append(Evidence(
                document_id=doc_id,
                quotes=ev_data["quotes"],
                evidence_grade=ev_data["evidence_grade"],
                recommendation_strength=ev_data["recommendation_strength"],
                grade_quotes=ev_data["grade_quotes"]
            ))

        # Calculate final aggregated metrics
        avg_spec = sum(data["specificity_scores"]) / len(data["specificity_scores"]) if data["specificity_scores"] else 5.0
        avg_act = sum(data["actionability_scores"]) / len(data["actionability_scores"]) if data["actionability_scores"] else 5.0
        avg_coh = sum(data["cohesion_scores"]) / len(data["cohesion_scores"]) if data["cohesion_scores"] else 5.0

        # Trust Score = Scientific Weight Sum + (Consensus Bonus)
        # Consensus bonus rewards findings appearing in multiple documents
        consensus_bonus = (data["consensus_count"] - 1) * 2.0
        trust_score = data["weighted_sum"] + max(0, consensus_bonus)

        # Calculate Certainty Level (Sikkerhet)
        if trust_score >= 30.0:
            certainty_level = "Høy"
        elif trust_score >= 15.0:
            certainty_level = "Moderat"
        else:
            certainty_level = "Lav"

        synthesized_findings.append(SynthesizedFinding(
            nursing_diagnosis=data["nursing_diagnosis"],
            interventions=data["interventions"],
            goals=data["goals"],
            FO=data["FO"],
            avg_specificity=round(avg_spec, 1),
            avg_actionability=round(avg_act, 1),
            avg_cohesion=round(avg_coh, 1),
            trust_score=round(trust_score, 1),
            certainty_level=certainty_level,
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
