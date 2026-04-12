"""
Consolidation and Synthesis logic for the VBP Workflow.
Handles the grouping of findings across documents and the assembly
of the final clinical report.
"""
import asyncio
from datetime import datetime
import json

from app.app_utils.telemetry import track_telemetry_span
from app.shared.config import config
from app.shared.tools import download_json_from_gcs, upload_json_to_gcs
from app.shared.taxonomy import get_norwegian_term
from app.shared.logging import VBPLogger
from app.shared.models import (
    Document,
    Evidence,
    ExcludedDocument,
    ExecutionSummary,
    MappedTerm,
    ProcessedDocument,
    ProcessedFinding,
    SynthesisResponse,
    SynthesizedFinding,
)

logger = VBPLogger("vbp_consolidation")

# Global Terminology Cache
# This is loaded once from GCS at the start of orchestration
taxonomy_cache = {
    "subsumption": {},
    "concepts": {}
}

# Block-list for generic root concepts that shouldn't be used for merging
BLOCKED_ROOT_PARENTS = {
    "138875005", # SNOMED CT Concept
    "404684003", # Clinical finding
    "71388002",  # Procedure
    "243796009", # Situation with explicit context
    "272379006", # Event
    "123037004", # Body structure
    "410607006", # Organism
}

def load_taxonomy_cache():
    """Initializes the taxonomy cache from GCS."""
    global taxonomy_cache
    remote_cache = download_json_from_gcs(config.TAXONOMY_CACHE_URI, config.PROJECT_ID)
    if remote_cache:
        taxonomy_cache["subsumption"].update(remote_cache.get("subsumption", {}))
        taxonomy_cache["concepts"].update(remote_cache.get("concepts", {}))
        logger.info(f"Taxonomy cache loaded: {len(taxonomy_cache['subsumption'])} links, {len(taxonomy_cache['concepts'])} concepts.")

def save_taxonomy_cache():
    """Persists the updated taxonomy cache back to GCS."""
    upload_json_to_gcs(taxonomy_cache, config.TAXONOMY_CACHE_URI, config.PROJECT_ID)
    logger.info("Taxonomy cache persisted to GCS.")

@track_telemetry_span("Consolidation: Group and Merge")
async def group_findings(processed_docs: list[ProcessedDocument], fhir_client=None) -> dict[str, dict]:
    """
    Groups individual findings by Functional Area (FO) and ICNP Concept ID.
    Implements Hierarchical Merging for both Diagnoses and Interventions to 
    distill a high-quality clinical template.
    """
    groups = {}
    global_id_cache = {}

    # Evidence Level Mapping (Knowledge Pyramid)
    LEVEL_WEIGHTS = {
        "Nivå 1": 10.0,
        "Nivå 2": 15.0,
        "Nivå 3": 5.0,
        "Nivå 4": 3.0,
    }

    # 1. Build Comprehensive Parent Mapping (Diagnoses & Interventions)
    parent_to_children = {}
    unique_ids = set()
    
    for doc_findings in processed_docs:
        for finding in doc_findings.mapped_findings:
            # Collect Diagnosis IDs
            d_cid = finding.mapped_nursing_diagnosis.ICNP_concept_id
            if d_cid and d_cid.isdigit():
                unique_ids.add(d_cid)
            
            # Collect Intervention IDs
            i_cid = finding.mapped_intervention.ICNP_concept_id
            if i_cid and i_cid.isdigit():
                unique_ids.add(i_cid)

    # Populate display cache and sibling merge map from pre-fetched taxonomy
    for cid in unique_ids:
        c_info = taxonomy_cache["concepts"].get(cid)
        if c_info:
            # Terminology Rule: Use Norwegian Preferred Term (PT) if in cache, else source term
            nor_term = get_norwegian_term(cid, None)
            global_id_cache[cid] = nor_term if nor_term else c_info.get("display", cid)
            
            # Register parents for Sibling Merging (Subsumption)
            parents = c_info.get("parent_ids", [])
            for p_id in parents:
                if p_id not in BLOCKED_ROOT_PARENTS:
                    if p_id not in parent_to_children:
                        parent_to_children[p_id] = []
                    parent_to_children[p_id].append(cid)

    # 2. Build Semantic Rewrite Map (FO||Child -> FO||Parent)
    global_rewrite_map = {}
    fo_id_map: dict[str, set[str]] = {}
    
    for doc in processed_docs:
        for finding in doc.mapped_findings:
            fo = finding.FO
            d_cid = finding.mapped_nursing_diagnosis.ICNP_concept_id
            i_cid = finding.mapped_intervention.ICNP_concept_id
            
            if fo not in fo_id_map:
                fo_id_map[fo] = set()
            if d_cid: fo_id_map[fo].add(d_cid)
            if i_cid: fo_id_map[fo].add(i_cid)

    for fo, ids in fo_id_map.items():
        for cid in ids:
            found_parent = False
            for p_id, children in parent_to_children.items():
                # We only merge siblings if at least 2 are present in this specific run/FO
                siblings_in_this_run = [c for c in children if c in ids]
                if cid in siblings_in_this_run and len(siblings_in_this_run) >= 2:
                    global_rewrite_map[f"{fo}||{cid}"] = f"{fo}||{p_id}"
                    
                    # Ensure parent display is resolved (PT or Cache)
                    if p_id not in global_id_cache:
                        p_info = taxonomy_cache["concepts"].get(p_id)
                        nor_parent = get_norwegian_term(p_id, None)
                        global_id_cache[p_id] = nor_parent if nor_parent else (p_info.get("display") if p_info else p_id)
                    found_parent = True
                    break
            if not found_parent:
                global_rewrite_map[f"{fo}||{cid}"] = f"{fo}||{cid}"

    # 3. Aggregation & Template Distillation
    for doc in processed_docs:
        doc_id = doc.source_document.document_id
        source_level = doc.source_document.evidence_level

        doc_weight = 1.0
        for key, weight in LEVEL_WEIGHTS.items():
            if key in source_level:
                doc_weight = weight
                break

        for finding in doc.mapped_findings:
            # Resolve Diagnosis Display (PT or Original)
            d_mapped = finding.mapped_nursing_diagnosis
            d_base_key = f"{finding.FO}||{d_mapped.ICNP_concept_id}" if d_mapped.ICNP_concept_id else f"{finding.FO}||{finding.nursing_diagnosis}"
            d_group_key = global_rewrite_map.get(d_base_key, d_base_key)
            final_d_id = d_group_key.split("||")[1] if "||" in d_group_key else ""

            if d_group_key not in groups:
                # If we have a Concept ID, use the cached PT, otherwise use raw text
                display_diag = global_id_cache.get(final_d_id, finding.nursing_diagnosis)

                groups[d_group_key] = {
                    "FO": finding.FO,
                    "nursing_diagnosis": MappedTerm(term=display_diag, ICNP_concept_id=final_d_id if final_d_id.isdigit() else ""),
                    "intervention_pool": {}, # ID -> {MappedTerm, weighted_score, evidence_count}
                    "goals": [],
                    "supporting_evidence": {},
                    "specificity_scores": [],
                    "actionability_scores": [],
                    "cohesion_scores": [],
                    "weighted_sum": 0.0,
                    "consensus_count": 0
                }

            # Distill Interventions (Apply Rewrite Map for Merging)
            i_mapped = finding.mapped_intervention
            i_base_key = f"{finding.FO}||{i_mapped.ICNP_concept_id}" if i_mapped.ICNP_concept_id else f"{finding.FO}||{finding.intervention}"
            i_group_key = global_rewrite_map.get(i_base_key, i_base_key)
            final_i_id = i_group_key.split("||")[1] if "||" in i_group_key else ""
            display_int = global_id_cache.get(final_i_id, finding.intervention)

            if i_group_key not in groups[d_group_key]["intervention_pool"]:
                groups[d_group_key]["intervention_pool"][i_group_key] = {
                    "mapped_term": MappedTerm(term=display_int, ICNP_concept_id=final_i_id if final_i_id.isdigit() else ""),
                    "weighted_quality": 0.0,
                    "evidence_level_sum": 0.0,
                    "consensus": 0
                }
            
            # Update Intervention Metadata for Ranking
            int_meta = groups[d_group_key]["intervention_pool"][i_group_key]
            int_meta["consensus"] += 1
            int_meta["evidence_level_sum"] += doc_weight
            if finding.auditor_rating:
                int_meta["weighted_quality"] += finding.auditor_rating.actionability_score

            # Standard Aggregate for Evidence and Goals
            if finding.mapped_goal not in groups[d_group_key]["goals"]:
                groups[d_group_key]["goals"].append(finding.mapped_goal)

            if doc_id not in groups[d_group_key]["supporting_evidence"]:
                groups[d_group_key]["supporting_evidence"][doc_id] = {
                    "quotes": [],
                    "evidence_grade": finding.evidence_grade,
                    "recommendation_strength": finding.recommendation_strength,
                    "grade_quotes": finding.grade_quotes
                }

            for quote in finding.quotes:
                if quote not in groups[d_group_key]["supporting_evidence"][doc_id]["quotes"]:
                    groups[d_group_key]["supporting_evidence"][doc_id]["quotes"].append(quote)

            if finding.auditor_rating:
                groups[d_group_key]["specificity_scores"].append(finding.auditor_rating.specificity_score)
                groups[d_group_key]["actionability_scores"].append(finding.auditor_rating.actionability_score)
                groups[d_group_key]["cohesion_scores"].append(finding.auditor_rating.cohesion_score)

            groups[d_group_key]["weighted_sum"] += doc_weight
            groups[d_group_key]["consensus_count"] += 1

    # 4. Final Ranking & Selection (Distilling the Template)
    final_output = {}
    for diag_key, data in groups.items():
        # Rank interventions: Priority = (Actionability * 0.4) + (Evidence Level * 0.6)
        ranked_ints = []
        for int_key, int_meta in data["intervention_pool"].items():
            avg_quality = int_meta["weighted_quality"] / int_meta["consensus"] if int_meta["consensus"] > 0 else 5.0
            rank_score = (avg_quality * 0.4) + (int_meta["evidence_level_sum"] * 0.6)
            ranked_ints.append((int_meta["mapped_term"], rank_score))
        
        # Sort by rank and keep the terms
        ranked_ints.sort(key=lambda x: x[1], reverse=True)
        data["interventions"] = [x[0] for x in ranked_ints]
        del data["intervention_pool"]
        final_output[diag_key] = data

    return final_output

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
    """Assembles the final SynthesisResponse object."""
    synthesized_findings = []

    for _group_key, data in grouped_data.items():
        evidence_list = []
        for doc_id, ev_data in data["supporting_evidence"].items():
            evidence_list.append(Evidence(
                document_id=doc_id,
                quotes=ev_data["quotes"],
                evidence_grade=ev_data["evidence_grade"],
                recommendation_strength=ev_data["recommendation_strength"],
                grade_quotes=ev_data["grade_quotes"]
            ))

        avg_spec = sum(data["specificity_scores"]) / len(data["specificity_scores"]) if data["specificity_scores"] else 5.0
        avg_act = sum(data["actionability_scores"]) / len(data["actionability_scores"]) if data["actionability_scores"] else 5.0
        avg_coh = sum(data["cohesion_scores"]) / len(data["cohesion_scores"]) if data["cohesion_scores"] else 5.0

        consensus_bonus = (data["consensus_count"] - 1) * 2.0
        trust_score = data["weighted_sum"] + max(0, consensus_bonus)

        if trust_score >= 30.0: certainty_level = "H\u00f8y"
        elif trust_score >= 15.0: certainty_level = "Moderat"
        else: certainty_level = "Lav"

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
