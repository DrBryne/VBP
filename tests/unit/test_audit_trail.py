from datetime import datetime

import pytest

from app.shared.consolidation import (
    finalize_synthesis,
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
async def test_norwegian_audit_trail():
    """
    Verifies that a document whose findings are 100% pruned correctly moves to
    excluded_documents with a Norwegian reason.
    """
    # 1. Setup
    taxonomy_cache["concepts"] = {"GENERIC_ID": {"display": "Hand Hygiene"}}
    GENERIC_NURSING_IDS.add("GENERIC_ID")
    norwegian_refset_ids.update({"GENERIC_ID", "PEARL_ID"})

    # Document A: Only generic findings (will be pruned if Pearl exists in FO)
    doc_a = Document(document_id="doc_a", source_uri="gs://test/a.pdf", title="Generic Doc", evidence_level="Nivå 1", publication_year=2024, doi="10.1234/a")
    # Document B: Provides the Pearl that triggers the pruning
    doc_b = Document(document_id="doc_b", source_uri="gs://test/b.pdf", title="Pearl Doc", evidence_level="Nivå 1", publication_year=2024, doi="10.1234/b")

    def create_finding(fid, diag, cid, spec, doc_id):
        return ProcessedFinding(
            finding_id=fid, document_id=doc_id, nursing_diagnosis=diag,
            intervention="Action", goal="Goal", supporting_sentence_ids=["S1"],
            clinical_specificity=spec, actionability_score=8,
            mapped_nursing_diagnosis=MappedTerm(term=diag, ICNP_concept_id=cid),
            mapped_intervention=MappedTerm(term="Action", ICNP_concept_id=""),
            mapped_goal=MappedTerm(term="Goal", ICNP_concept_id=""),
            FO=FunctionalArea.FO3, quotes=["Quote"],
            auditor_rating=AuditorRating(finding_id=fid, specificity_score=spec, actionability_score=8, cohesion_score=9, auditor_comment="Good")
        )

    f_generic = create_finding("f1", "Hand Hygiene", "GENERIC_ID", 3, "doc_a")
    f_pearl1 = create_finding("f2", "ALS Pearl 1", "PEARL_ID_1", 9, "doc_b")
    f_pearl2 = create_finding("f3", "ALS Pearl 2", "PEARL_ID_2", 8, "doc_b")

    processed_docs = [
        ProcessedDocument(source_document=doc_a, mapped_findings=[f_generic]),
        ProcessedDocument(source_document=doc_b, mapped_findings=[f_pearl1, f_pearl2])
    ]

    # 2. Execute Consolidation
    grouped_data = await group_findings(processed_docs, fhir_client=None)

    # 3. Finalize Synthesis (This is where doc exclusion happens)
    response = finalize_synthesis(
        target_group="ALS",
        source_uri="gs://test/",
        total_files_in_uri=2,
        execution_start_time=datetime.now(),
        execution_end_time=datetime.now(),
        grouped_data=grouped_data,
        source_documents=[doc_a, doc_b],
        excluded_documents=[]
    )

    # 4. Assertions
    # Document B should be successful
    assert len(response.source_documents) == 1
    assert response.source_documents[0].document_id == "doc_b"

    # Document A should be EXCLUDED
    assert len(response.excluded_documents) == 1
    excluded = response.excluded_documents[0]
    assert excluded.title == "Generic Doc"

    # Verify Norwegian Reason
    expected_reason = "Dokumentets funn ble filtrert bort under konsolidering p\u00e5 grunn av lav spesifisitet eller manglende konsensus med andre kilder."
    assert excluded.justification == expected_reason

    print("\n✅ Audit Trail Test Passed: Pruned documents correctly reported in Norwegian.")
