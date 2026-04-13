import asyncio

from app.shared.logging import VBPLogger
from app.shared.models import (
    AuditorRating,
    ClinicalFinding,
    DiagnosisMappingResponse,
    FunctionalAreaResponse,
    GoalMappingResponse,
    InterventionMappingResponse,
    MappedTerm,
    ProcessedFinding,
    WorkflowProgress,
)
from app.shared.taxonomy import get_default_fo, load_valid_icnp_ids

logger = VBPLogger("taxonomy_validator")

def validate_taxonomy(
    finding_map: dict[str, tuple[ClinicalFinding, AuditorRating | None, float]],
    diag_mappings: DiagnosisMappingResponse,
    int_mappings: InterventionMappingResponse,
    goal_mappings: GoalMappingResponse,
    fo_mappings: FunctionalAreaResponse,
    doc_id: str,
    filename: str,
    progress_state: WorkflowProgress,
    state_lock: asyncio.Lock
) -> tuple[list[ProcessedFinding], int]:
    """Cross-references LLM mapping results against the master ICNP dictionary."""
    valid_icnp_ids = load_valid_icnp_ids()
    processed_findings = []
    taxonomy_error_count = 0

    diag_lookup = {res.finding_id: res.nursing_diagnosis for res in diag_mappings.results} if diag_mappings else {}
    int_lookup = {res.finding_id: res.intervention for res in int_mappings.results} if int_mappings else {}
    goal_lookup = {res.finding_id: res.goal for res in goal_mappings.results} if goal_mappings else {}
    fo_lookup = {res.finding_id: res.FO for res in fo_mappings.results} if fo_mappings else {}

    for f_id, (original, auditor_rating, quality_score) in finding_map.items():
        fo_val = fo_lookup.get(f_id, get_default_fo())

        def resolve(orig_val, mapping_field, field_name, current_f_id=f_id):
            nonlocal taxonomy_error_count
            if mapping_field and mapping_field.term:
                concept_id = mapping_field.ICNP_concept_id
                if concept_id and concept_id not in valid_icnp_ids:
                    # We no longer clear the ID immediately. We flag it as 'Out-of-Refset'
                    # and let the Consolidator attempt enrichment via the Australian FHIR API.
                    logger.info(
                        f"[Taxonomy Validation] ID '{concept_id}' is not in local Norwegian Refsets. Preserving for enrichment.",
                        field=field_name, finding_id=current_f_id
                    )
                return MappedTerm(term=mapping_field.term, ICNP_concept_id=concept_id)
            return MappedTerm(term=orig_val, ICNP_concept_id="")

        processed_findings.append(ProcessedFinding(
            finding_id=f_id,
            document_id=doc_id,
            nursing_diagnosis=original.nursing_diagnosis,
            intervention=original.intervention,
            goal=original.goal,
            supporting_sentence_ids=original.supporting_sentence_ids,
            recommendation_strength=original.recommendation_strength,
            evidence_grade=original.evidence_grade,
            grade_sentence_ids=original.grade_sentence_ids,
            clinical_specificity=original.clinical_specificity,
            actionability_score=original.actionability_score,
            quotes=original.quotes,
            grade_quotes=original.grade_quotes,
            mapped_nursing_diagnosis=resolve(original.nursing_diagnosis, diag_lookup.get(f_id), "nursing_diagnosis"),
            mapped_intervention=resolve(original.intervention, int_lookup.get(f_id), "intervention"),
            mapped_goal=resolve(original.goal, goal_lookup.get(f_id), "goal"),
            FO=fo_val,
            auditor_rating=auditor_rating,
            weighted_quality_score=quality_score
        ))

    return processed_findings, taxonomy_error_count
