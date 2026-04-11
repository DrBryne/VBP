import asyncio
import pytest
from app.shared.processing import validate_taxonomy
from app.shared.models import (
    ClinicalFinding,
    DiagnosisMappingResponse,
    DiagnosisMapping,
    InterventionMappingResponse,
    GoalMappingResponse,
    FunctionalAreaResponse,
    FunctionalArea,
    MappedTerm,
    WorkflowProgress,
    AuditorRating
)

@pytest.mark.asyncio
async def test_validate_taxonomy_hallucinated_id():
    """
    Test that validate_taxonomy strips hallucinated ICNP IDs but keeps the term.
    """
    # 1. Setup raw finding map
    finding_id = "f1"
    original = ClinicalFinding(
        nursing_diagnosis="Svelgevansker",
        intervention="Tilpasset kost",
        goal="Sikker svelging",
        supporting_sentence_ids=["S1"],
        clinical_specificity=10,
        actionability_score=10,
        quotes=["Some text"]
    )
    # Finding map format: {f_id: (original, auditor_rating, quality_score)}
    finding_map = {finding_id: (original, None, 8.0)}
    
    # 2. Setup mappings with a HALLUCINATED ID (not in diagnoses.txt/interventions.txt)
    # Note: validate_taxonomy uses load_valid_icnp_ids() which reads from the agent data files.
    diag_mappings = DiagnosisMappingResponse(results=[
        DiagnosisMapping(
            finding_id=finding_id,
            nursing_diagnosis=MappedTerm(term="Dysfagi", ICNP_concept_id="999999999") # Hallucination
        )
    ])
    
    progress_state = WorkflowProgress()
    state_lock = asyncio.Lock()
    
    processed_findings, error_count = validate_taxonomy(
        finding_map=finding_map,
        diag_mappings=diag_mappings,
        int_mappings=None,
        goal_mappings=None,
        fo_mappings=None,
        doc_id="doc1",
        filename="test.pdf",
        progress_state=progress_state,
        state_lock=state_lock
    )
    
    assert len(processed_findings) == 1
    f = processed_findings[0]
    
    # Assert ID was stripped because it's not valid
    assert f.mapped_nursing_diagnosis.term == "Dysfagi"
    assert f.mapped_nursing_diagnosis.ICNP_concept_id == ""
    assert error_count == 1

@pytest.mark.asyncio
async def test_validate_taxonomy_missing_mapping():
    """
    Test that validate_taxonomy falls back to original text if mapping is missing.
    """
    finding_id = "f1"
    original = ClinicalFinding(
        nursing_diagnosis="Angst",
        intervention="Samtale",
        goal="Redusert angst",
        supporting_sentence_ids=["S1"],
        clinical_specificity=5,
        actionability_score=5,
        quotes=["Some text"]
    )
    finding_map = {finding_id: (original, None, 5.0)}
    
    # Mappings are empty/None
    processed_findings, error_count = validate_taxonomy(
        finding_map=finding_map,
        diag_mappings=None,
        int_mappings=None,
        goal_mappings=None,
        fo_mappings=None,
        doc_id="doc1",
        filename="test.pdf",
        progress_state=WorkflowProgress(),
        state_lock=asyncio.Lock()
    )
    
    assert len(processed_findings) == 1
    f = processed_findings[0]
    
    # Should fall back to original
    assert f.mapped_nursing_diagnosis.term == "Angst"
    assert f.mapped_nursing_diagnosis.ICNP_concept_id == ""
    # Should use default FO if missing
    assert f.FO == "12. Annet/legedelegerte aktiviteter"
