import pytest

from app.shared.consolidation import (
    group_findings,
    norwegian_refset_ids,
    taxonomy_cache,
)
from app.shared.models import (
    AuditorRating,
    Document,
    FunctionalArea,
    MappedTerm,
    ProcessedDocument,
    ProcessedFinding,
)
from app.shared.taxonomy import GENERIC_NURSING_IDS


@pytest.mark.asyncio
async def test_admission_and_pruning_logic():
    """
    1. Tests that weak findings (Nivå 4, single document) are dropped.
    2. Tests that strong findings (Nivå 1) are kept.
    3. Tests that generic nursing findings are pruned if specific pearls exist.
    """
    # 1. Setup Mock Taxonomy Cache
    taxonomy_cache["concepts"] = {
        "ALS_PEARL": {"display": "ALS Specific Finding", "parent_ids": []},
        "GENERIC_ID": {"display": "Hand Hygiene", "parent_ids": []}
    }
    # Add GENERIC_ID to the global generic list for the test
    GENERIC_NURSING_IDS.add("GENERIC_ID")

    norwegian_refset_ids.clear()
    norwegian_refset_ids.update({"ALS_PEARL", "GENERIC_ID", "WEAK_ID"})

    # 2. Setup Mock Documents
    doc_strong = Document(document_id="d1", source_uri="gs://test/1.pdf", title="Meta", evidence_level="Nivå 1", publication_year=2024, doi="10.1234/1")
    doc_weak = Document(document_id="d2", source_uri="gs://test/2.pdf", title="Opinion", evidence_level="Nivå 4", publication_year=2024, doi="10.1234/2")

    def create_finding(fid, diag, cid, spec, doc_id, fo=FunctionalArea.FO3):
        return ProcessedFinding(
            finding_id=fid, document_id=doc_id, nursing_diagnosis=diag,
            intervention="Action", goal="Goal", supporting_sentence_ids=["S1"],
            clinical_specificity=spec, actionability_score=8,
            mapped_nursing_diagnosis=MappedTerm(term=diag, ICNP_concept_id=cid),
            mapped_intervention=MappedTerm(term="Action", ICNP_concept_id=""),
            mapped_goal=MappedTerm(term="Goal", ICNP_concept_id=""),
            FO=fo, quotes=["Quote"],
            auditor_rating=AuditorRating(finding_id=fid, specificity_score=spec, actionability_score=8, cohesion_score=9, auditor_comment="Good")
        )

    # Findings:
    # f1: Strong evidence, High specificity -> SHOULD KEEP
    # f1b: Second high-specificity finding -> TRIGGERS PRUNING
    # f2: Strong evidence, GENERIC -> SHOULD PRUNE (because f1 & f1b are in same FO)
    # f3: Weak evidence, Lonely -> SHOULD DROP (Admission Control)

    f1 = create_finding("f1", "ALS Specific 1", "ALS_PEARL_1", 9, "d1", fo=FunctionalArea.FO3)
    f1b = create_finding("f1b", "ALS Specific 2", "ALS_PEARL_2", 8, "d1", fo=FunctionalArea.FO3)
    f2 = create_finding("f2", "Hand Hygiene", "GENERIC_ID", 3, "d1", fo=FunctionalArea.FO3)
    f3 = create_finding("f3", "Weak Finding", "WEAK_ID", 6, "d2", fo=FunctionalArea.FO1)

    processed_docs = [
        ProcessedDocument(source_document=doc_strong, mapped_findings=[f1, f1b, f2]),
        ProcessedDocument(source_document=doc_weak, mapped_findings=[f3])
    ]

    # 3. Execute
    grouped_data = await group_findings(processed_docs, fhir_client=None)

    # 4. Assertions

    # Verify Admission: ALS Specifics (Nivå 1) are kept
    found_pearl1 = any("ALS_PEARL_1" in k for k in grouped_data.keys())
    found_pearl2 = any("ALS_PEARL_2" in k for k in grouped_data.keys())
    assert found_pearl1 and found_pearl2, "Expected ALS_PEARLs to pass admission (Nivå 1)"

    # Verify Admission: Weak Finding (Nivå 4, single doc) is DROPPED
    found_weak = any("WEAK_ID" in k for k in grouped_data.keys())
    assert not found_weak, "Expected WEAK_ID to be dropped (Admission Control: Level 4, low consensus)"

    # Verify FO Pruning: Hand Hygiene is DROPPED because ALS Specific Pearl is in same FO
    found_generic = any("GENERIC_ID" in k for k in grouped_data.keys())
    assert not found_generic, "Expected GENERIC_ID to be pruned because high-specificity finding exists in FO3"

    print("\n✅ Admission and Pruning Test Passed: Weak noise removed, strong specialized findings kept.")
