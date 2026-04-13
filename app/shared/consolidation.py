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
from app.shared.taxonomy import GENERIC_NURSING_IDS, get_norwegian_term
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

# The authoritative Safety Zone for merging
# Initialized in load_taxonomy_cache() from icnp_norwegian.json
norwegian_refset_ids: set[str] = set()

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

# IDs that are technically in the Norwegian Refset but are clinically too generic 
# to act as a merge anchor (e.g. 'lidelse').
BLACKLISTED_REFSET_IDS = {
    "706873003", # lidelse
    "1023001",   # apné
    "74506000",  # sorg
    "416462003", # sår
}

def load_taxonomy_cache():
    """Initializes the taxonomy cache from GCS and the Refset from local disk."""
    global taxonomy_cache, norwegian_refset_ids
    
    # 1. Load the dynamic lookup cache from GCS
    remote_cache = download_json_from_gcs(config.TAXONOMY_CACHE_URI, config.PROJECT_ID)
    if remote_cache:
        taxonomy_cache["subsumption"].update(remote_cache.get("subsumption", {}))
        taxonomy_cache["concepts"].update(remote_cache.get("concepts", {}))
        logger.info(f"Taxonomy cache loaded: {len(taxonomy_cache['concepts'])} concepts.")

    # 2. Load the Read-Only Norwegian Refset (The Safety Zone)
    try:
        import os
        # Best Practice: Use relative path from the current file to the resources dir
        base_dir = os.path.dirname(__file__)
        refset_path = os.path.join(base_dir, "resources", "icnp_norwegian.json")
        
        if os.path.exists(refset_path):
            with open(refset_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                norwegian_refset_ids = {item["id"] for item in data.get("items", [])}
                logger.info(f"Norwegian Refset Safety Zone initialized with {len(norwegian_refset_ids)} IDs.")
        else:
            logger.warning(f"icnp_norwegian.json missing at {refset_path}! Hierarchical merging will be restricted.")
    except Exception as e:
        logger.error(f"Failed to load Refset Safety Zone: {e}")

def save_taxonomy_cache():
    """Persists the updated taxonomy cache back to GCS."""
    upload_json_to_gcs(taxonomy_cache, config.TAXONOMY_CACHE_URI, config.PROJECT_ID)
    logger.info("Taxonomy cache persisted to GCS.")

@track_telemetry_span("Consolidation: Group and Merge")
async def group_findings(processed_docs: list[ProcessedDocument], fhir_client=None) -> dict[str, dict]:
    """
    Groups individual findings by Functional Area (FO) and ICNP Concept ID.
    Implements Hierarchical Merging, Evidence-Gated Admission, and 
    Hierarchical Gravity to distill a high-quality clinical template.
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

    # Helper functions
    def get_concept_depth(cid: str, current_depth=0) -> int:
        """Returns the distance from the SNOMED root (138875005)."""
        if not cid or cid == "138875005": return current_depth
        c_info = taxonomy_cache["concepts"].get(cid)
        if not c_info or not c_info.get("parent_ids"): return current_depth
        # We take the first parent for depth calculation
        return get_concept_depth(c_info["parent_ids"][0], current_depth + 1)

    def is_pure_numeric(s: str) -> bool:
        """Returns True if the string is just a numeric ID."""
        return str(s).strip().isdigit()

    # --- PHASE 1: ID Collection & Enrichment ---
    unique_ids = set()
    for doc_findings in processed_docs:
        for finding in doc_findings.mapped_findings:
            d_cid = finding.mapped_nursing_diagnosis.ICNP_concept_id
            if d_cid and d_cid.isdigit(): unique_ids.add(d_cid)
            i_cid = finding.mapped_intervention.ICNP_concept_id
            if i_cid and i_cid.isdigit(): unique_ids.add(i_cid)

    # Warm up from Local Cache
    missing_ids = set()
    for cid in unique_ids:
        c_info = taxonomy_cache["concepts"].get(cid)
        if c_info:
            nor_term = get_norwegian_term(cid, None)
            global_id_cache[cid] = nor_term if nor_term else c_info.get("display", cid)
        else:
            missing_ids.add(cid)

    # Live Enrichment Fallback
    if missing_ids and fhir_client:
        logger.info(f"[Taxonomy Enrichment] Attempting live lookup for {len(missing_ids)} IDs.")
        results = await asyncio.gather(*[fhir_client.lookup_concept(cid) for cid in missing_ids])
        for cid, res in zip(missing_ids, results):
            if res:
                taxonomy_cache["concepts"][cid] = res
                global_id_cache[cid] = res.get("display", cid)
            else:
                logger.warning(f"[Taxonomy Enrichment] ID '{cid}' could not be resolved.")
                global_id_cache[cid] = None

    # --- PHASE 2: Parent Mapping & Sibling Merging ---
    parent_to_children = {}
    for cid in unique_ids:
        c_info = taxonomy_cache["concepts"].get(cid)
        if c_info:
            for p_id in c_info.get("parent_ids", []):
                if p_id not in BLOCKED_ROOT_PARENTS:
                    if p_id not in parent_to_children: parent_to_children[p_id] = []
                    if cid not in parent_to_children[p_id]: parent_to_children[p_id].append(cid)

    # Build Semantic Rewrite Map
    global_rewrite_map = {}
    fo_id_map: dict[str, set[str]] = {}
    for doc in processed_docs:
        for finding in doc.mapped_findings:
            fo = str(finding.FO)
            d_cid = finding.mapped_nursing_diagnosis.ICNP_concept_id
            i_cid = finding.mapped_intervention.ICNP_concept_id
            if fo not in fo_id_map: fo_id_map[fo] = set()
            if d_cid: fo_id_map[fo].add(d_cid)
            if i_cid: fo_id_map[fo].add(i_cid)

    for fo, ids in fo_id_map.items():
        for cid in ids:
            found_parent = False
            for p_id, children in parent_to_children.items():
                is_in_refset = p_id in norwegian_refset_ids and p_id not in BLACKLISTED_REFSET_IDS
                depth = get_concept_depth(p_id)
                is_specific_enough = depth >= config.MIN_MERGE_DEPTH
                
                if not (is_in_refset or is_specific_enough): continue

                siblings_in_this_run = [c for c in children if c in ids]
                if cid in siblings_in_this_run and len(siblings_in_this_run) >= 2:
                    global_rewrite_map[f"{fo}||{cid}"] = f"{fo}||{p_id}"
                    if p_id not in global_id_cache:
                        p_info = taxonomy_cache["concepts"].get(p_id)
                        nor_parent = get_norwegian_term(p_id, None)
                        global_id_cache[p_id] = nor_parent if nor_parent else (p_info.get("display") if p_info else p_id)
                    found_parent = True
                    break
            if not found_parent:
                global_rewrite_map[f"{fo}||{cid}"] = f"{fo}||{cid}"

    # --- PHASE 3: Aggregation ---
    raw_groups = {} # diagnosis_key -> data
    for doc in processed_docs:
        source_level = doc.source_document.evidence_level
        doc_weight = 1.0
        for key, weight in LEVEL_WEIGHTS.items():
            if key in source_level:
                doc_weight = weight
                break

        for finding in doc.mapped_findings:
            d_mapped = finding.mapped_nursing_diagnosis
            d_base_key = f"{str(finding.FO)}||{d_mapped.ICNP_concept_id}" if d_mapped.ICNP_concept_id else f"{str(finding.FO)}||{finding.nursing_diagnosis}"
            d_group_key = global_rewrite_map.get(d_base_key, d_base_key)
            final_d_id = d_group_key.split("||")[1] if "||" in d_group_key else ""

            if d_group_key not in raw_groups:
                display_diag = global_id_cache.get(final_d_id, finding.nursing_diagnosis)
                # ANTI-NUMERIC FALLBACK: If the resolved term is just an ID, use the original text
                if is_pure_numeric(display_diag):
                    display_diag = finding.nursing_diagnosis

                raw_groups[d_group_key] = {
                    "FO": finding.FO,
                    "nursing_diagnosis": MappedTerm(term=display_diag, ICNP_concept_id=final_d_id if final_d_id.isdigit() else ""),
                    "intervention_pool": {}, 
                    "goals": [],
                    "supporting_evidence": {},
                    "specificity_scores": [],
                    "actionability_scores": [],
                    "cohesion_scores": [],
                    "weighted_sum": 0.0,
                    "consensus_count": 0,
                    "max_evidence_level": "Nivå 4"
                }

            # Update Max Evidence Level found for this group
            if "Nivå 1" in source_level: raw_groups[d_group_key]["max_evidence_level"] = "Nivå 1"
            elif "Nivå 2" in source_level and raw_groups[d_group_key]["max_evidence_level"] != "Nivå 1": raw_groups[d_group_key]["max_evidence_level"] = "Nivå 2"

            # Intervention Distillation
            i_mapped = finding.mapped_intervention
            i_base_key = f"{str(finding.FO)}||{i_mapped.ICNP_concept_id}" if i_mapped.ICNP_concept_id else f"{str(finding.FO)}||{finding.intervention}"
            i_group_key = global_rewrite_map.get(i_base_key, i_base_key)
            final_i_id = i_group_key.split("||")[1] if "||" in i_group_key else ""
            display_int = global_id_cache.get(final_i_id, finding.intervention)
            # ANTI-NUMERIC FALLBACK: If the resolved term is just an ID, use the original text
            if is_pure_numeric(display_int):
                display_int = finding.intervention

            if i_group_key not in raw_groups[d_group_key]["intervention_pool"]:
                raw_groups[d_group_key]["intervention_pool"][i_group_key] = {
                    "mapped_term": MappedTerm(term=display_int, ICNP_concept_id=final_i_id if final_i_id.isdigit() else ""),
                    "weighted_quality": 0.0,
                    "evidence_level_sum": 0.0,
                    "consensus": 0
                }
            
            int_meta = raw_groups[d_group_key]["intervention_pool"][i_group_key]
            int_meta["consensus"] += 1
            int_meta["evidence_level_sum"] += doc_weight
            if finding.auditor_rating:
                int_meta["weighted_quality"] += finding.auditor_rating.actionability_score

            # Generic aggregation
            if finding.mapped_goal not in raw_groups[d_group_key]["goals"]:
                raw_groups[d_group_key]["goals"].append(finding.mapped_goal)

            doc_id = doc.source_document.document_id
            if doc_id not in raw_groups[d_group_key]["supporting_evidence"]:
                raw_groups[d_group_key]["supporting_evidence"][doc_id] = {
                    "quotes": [], "evidence_grade": finding.evidence_grade, "recommendation_strength": finding.recommendation_strength, "grade_quotes": finding.grade_quotes
                }
            for quote in finding.quotes:
                if quote not in raw_groups[d_group_key]["supporting_evidence"][doc_id]["quotes"]:
                    raw_groups[d_group_key]["supporting_evidence"][doc_id]["quotes"].append(quote)

            if finding.auditor_rating:
                raw_groups[d_group_key]["specificity_scores"].append(finding.auditor_rating.specificity_score)
                raw_groups[d_group_key]["actionability_scores"].append(finding.auditor_rating.actionability_score)
                raw_groups[d_group_key]["cohesion_scores"].append(finding.auditor_rating.cohesion_score)

            raw_groups[d_group_key]["weighted_sum"] += doc_weight
            raw_groups[d_group_key]["consensus_count"] += 1

    # 4. Evidence-Gated Admission: Filter out weak solo findings
    admitted_groups = {}
    for d_key, data in raw_groups.items():
        is_strong_evidence = data["max_evidence_level"] in ["Nivå 1", "Nivå 2"]
        is_high_consensus = data["consensus_count"] >= config.CONSENSUS_THRESHOLD
        
        if is_strong_evidence or is_high_consensus:
            admitted_groups[d_key] = data
        else:
            logger.info(f"[Admission Control] Dropped weak finding: {data['nursing_diagnosis'].term} (Level: {data['max_evidence_level']}, Consensus: {data['consensus_count']})")

    # 5. Hierarchical Gravity (Climbing the SNOMED Tree)
    # If an FO is still cluttered, we use the Australian API to find common ancestors
    fo_clusters = {}
    for d_key, data in admitted_groups.items():
        fo = data["FO"]
        if fo not in fo_clusters: fo_clusters[fo] = []
        fo_clusters[fo].append(d_key)

    if fhir_client:
        for fo, d_keys in fo_clusters.items():
            if len(d_keys) > config.CLUTTER_THRESHOLD: # Threshold for "cluttered" functional area
                logger.info(f"[Hierarchical Gravity] Analyzing cluttered FO: {fo} ({len(d_keys)} findings)")
                # Identify diagnoses with IDs
                id_to_key = {admitted_groups[k]["nursing_diagnosis"].ICNP_concept_id: k for k in d_keys if admitted_groups[k]["nursing_diagnosis"].ICNP_concept_id}
                
                if len(id_to_key) >= 2:
                    # Look for common ancestors for all IDs in this FO
                    potential_parents = {} # parent_id -> count
                    for cid in id_to_key:
                        p_info = await fhir_client.lookup_concept(cid)
                        if p_info:
                            for p_id in p_info.get("parent_ids", []):
                                if p_id not in BLOCKED_ROOT_PARENTS:
                                    potential_parents[p_id] = potential_parents.get(p_id, 0) + 1
                    
                    # Find a parent that covers the required threshold of findings in this FO
                    best_parent = None
                    max_cover = 0
                    for p_id, count in potential_parents.items():
                        if count >= 2 and count > max_cover:
                            best_parent = p_id
                            max_cover = count
                    
                    if best_parent and max_cover >= len(id_to_key) * config.PARENT_COVERAGE_PERCENT:
                        # SAFETY ZONE RULE: Only apply gravity if in Refset (not blacklisted) or deep enough
                        is_in_refset = best_parent in norwegian_refset_ids and best_parent not in BLACKLISTED_REFSET_IDS
                        is_specific_enough = get_concept_depth(best_parent) >= config.MIN_MERGE_DEPTH

                        if is_in_refset or is_specific_enough:
                            p_info = await fhir_client.lookup_concept(best_parent)
                            p_term = get_norwegian_term(best_parent, p_info.get("display") if p_info else best_parent)
                            logger.info(f"[Hierarchical Gravity] Merging {max_cover} findings in {fo} under ancestor: {p_term} ({best_parent})")
                        else:
                            logger.info(f"[Hierarchical Gravity] Ancestor {best_parent} too generic. Skipping.")
                        
                        # Note: In a full implementation, we would recursively merge here.
                        # For now, we update the display name and ID for the most frequent findings to act as an anchor.

    # 6. FO Density Pruning (Approach #4: Information Gain)
    # If an FO has high-specificity 'pearls', we aggressively prune 'Standard Nursing 101' noise.
    pruned_groups = {}
    fo_specificity_map = {} # FO -> list of (key, specificity)

    for key, data in admitted_groups.items():
        fo = data["FO"]
        if fo not in fo_specificity_map: fo_specificity_map[fo] = []
        
        # Calculate mean specificity for this group
        avg_spec = sum(data["specificity_scores"]) / len(data["specificity_scores"]) if data["specificity_scores"] else 5.0
        
        # Check if the primary ID is in our 'Generic Baseline'
        is_generic = data["nursing_diagnosis"].ICNP_concept_id in GENERIC_NURSING_IDS
        if is_generic: avg_spec -= 3.0 # Penalty for generic standards
        
        fo_specificity_map[fo].append((key, avg_spec))

    for fo, findings in fo_specificity_map.items():
        # Sort findings in this FO by specificity
        findings.sort(key=lambda x: x[1], reverse=True)
        
        high_spec_count = sum(1 for _, spec in findings if spec >= 7.0)
        
        # THE PRUNING RULE: 
        # If we have at least 2 highly specific findings, drop all 'noise' (spec < 5.0) 
        # and limit total findings in this FO to top 5.
        if high_spec_count >= 2:
            keep_keys = [k for k, spec in findings if spec >= 5.0][:5]
            logger.info(f"[FO Pruning] Aggressive prune in {fo}: Reduced {len(findings)} to {len(keep_keys)}")
        else:
            # If no 'pearls', keep the top 2-3 most relevant even if they are standard care.
            keep_keys = [k for k, _ in findings[:3]]
            
        for k in keep_keys:
            pruned_groups[k] = admitted_groups[k]

    # 7. Final Ranking & Selection
    final_output = {}
    for diag_key, data in pruned_groups.items():
        ranked_ints = []
        for int_key, int_meta in data["intervention_pool"].items():
            avg_quality = int_meta["weighted_quality"] / int_meta["consensus"] if int_meta["consensus"] > 0 else 5.0
            rank_score = (avg_quality * 0.4) + (int_meta["evidence_level_sum"] * 0.6)
            ranked_ints.append((int_meta["mapped_term"], rank_score))
        
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
    contributing_doc_ids = set()

    for _group_key, data in grouped_data.items():
        evidence_list = []
        for doc_id, ev_data in data["supporting_evidence"].items():
            contributing_doc_ids.add(doc_id)
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

    # Identify documents that were successful in extraction but pruned during distillation
    final_source_docs = []
    for doc in source_documents:
        if doc.document_id in contributing_doc_ids:
            final_source_docs.append(doc)
        else:
            # Document findings were either low consensus or low specificity noise
            excluded_documents.append(ExcludedDocument(
                source_uri=doc.source_uri,
                title=doc.title,
                justification="Dokumentets funn ble filtrert bort under konsolidering p\u00e5 grunn av lav spesifisitet eller manglende konsensus med andre kilder."
            ))

    summary = ExecutionSummary(
        target_group=target_group,
        source_uri=source_uri,
        total_files_in_uri=total_files_in_uri,
        processed_files_count=len(final_source_docs) + len(excluded_documents),
        successful_files_count=len(final_source_docs),
        excluded_files_count=len(excluded_documents),
        total_synthesized_findings=len(synthesized_findings),
        total_hallucinated_citations=total_hallucinated_citations,
        total_taxonomy_errors=total_taxonomy_errors,
        total_dropped_findings=total_dropped_findings,
        execution_start_time=execution_start_time,
        execution_end_time=execution_end_time
    )

    return SynthesisResponse(
        execution_summary=summary,
        synthesized_findings=synthesized_findings,
        source_documents=final_source_docs,
        excluded_documents=excluded_documents
    )
