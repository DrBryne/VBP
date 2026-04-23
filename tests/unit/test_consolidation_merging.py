import pytest
from datetime import datetime
from app.shared.consolidation import group_findings, taxonomy_cache, norwegian_refset_ids
from app.shared.models import (
    ProcessedDocument, 
    ProcessedFinding, 
    Document, 
    MappedTerm, 
    FunctionalArea,
    AuditorRating
)

@pytest.mark.asyncio
async def test_sibling_merging_logic():
    """
    Verifies that two findings in the same Functional Area with different ICNP IDs
    are merged if they share a common parent in the Norwegian Refset.
    """
    taxonomy_cache["concepts"].clear()
    norwegian_refset_ids.clear()

    # 1. Setup Mock Taxonomy Cache
    # Child A (101) and Child B (102) share Parent P (900)
    taxonomy_cache["concepts"] = {
        "101": {"display": "Child A", "parent_ids": ["900"]},
        "102": {"display": "Child B", "parent_ids": ["900"]},
        "900": {"display": "Common Parent Term", "parent_ids": ["138875005"]}
    }
    
    # 2. Setup Refset Safety Zone
    # We must ensure the global set is actually populated for the logic to work
    norwegian_refset_ids.clear()
    norwegian_refset_ids.update({"900", "101", "102"})

    # 3. Setup Mock Processed Document
    # Use Nivå 1 to pass admission
    doc = Document(
        document_id="d1",
        source_uri="gs://test/doc1.pdf",
        title="Test Document",
        publication_year=2024,
        doi="10.1234/test",
        evidence_level="Nivå 1"
    )
    
    def create_finding(fid, diag, cid):
        return ProcessedFinding(
            finding_id=fid,
            document_id="d1",
            nursing_diagnosis=diag,
            intervention=f"Action {fid}",
            goal=f"Goal {fid}",
            supporting_sentence_ids=["S1"],
            clinical_specificity=6, # Low to avoid aggressive pruning
            actionability_score=8,
            mapped_nursing_diagnosis=MappedTerm(term=diag, ICNP_concept_id=cid),
            mapped_intervention=MappedTerm(term=f"Action {fid}", ICNP_concept_id=""),
            mapped_goal=MappedTerm(term=f"Goal {fid}", ICNP_concept_id=""),
            FO=FunctionalArea.FO3,
            quotes=[f"Quote {fid}"],
            auditor_rating=AuditorRating(
                finding_id=fid,
                specificity_score=6,
                actionability_score=8,
                cohesion_score=9,
                auditor_comment="Good"
            )
        )

    finding_a = create_finding("f1", "Child A", "101")
    finding_b = create_finding("f2", "Child B", "102")
    
    processed_docs = [
        ProcessedDocument(source_document=doc, mapped_findings=[finding_a, finding_b])
    ]
    
    # 4. Execute Consolidation
    grouped_data = await group_findings(processed_docs, fhir_client=None)
    
    # 5. Assertions
    # We expect exactly ONE group because they merged under parent 900
    assert len(grouped_data) == 1, f"Expected 1 merged group, but got {len(grouped_data)}"
    
    group_key = f"{FunctionalArea.FO3}||900"
    assert group_key in grouped_data
    
    merged_finding = grouped_data[group_key]
    assert merged_finding["nursing_diagnosis"].ICNP_concept_id == "900"
    assert merged_finding["nursing_diagnosis"].term == "Common Parent Term"
    
    # Verify that evidence from both children was preserved
    evidence = merged_finding["supporting_evidence"]["d1"]
    assert "Quote f1" in evidence["quotes"]
    assert "Quote f2" in evidence["quotes"]
    
    # Verify that interventions were aggregated (Wait, merged interventions logic)
    assert len(merged_finding["interventions"]) == 2
    
    print("\n✅ Consolidation Merge Test Passed: Successfully distilled two siblings into one parent.")
