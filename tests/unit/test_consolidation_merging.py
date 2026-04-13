import pytest
from datetime import datetime
from app.shared.consolidation import group_findings, taxonomy_cache
from app.shared.models import (
    ProcessedDocument, 
    ProcessedFinding, 
    Document, 
    MappedTerm, 
    FunctionalArea
)

@pytest.mark.asyncio
async def test_sibling_merging_logic():
    """
    Verifies that two findings in the same Functional Area with different ICNP IDs
    are merged if they share a common parent in the taxonomy_cache.
    """
    # 1. Setup Mock Taxonomy Cache
    # Child A (101) and Child B (102) share Parent P (900)
    taxonomy_cache["concepts"] = {
        "101": {"display": "Child A", "parent_ids": ["900"]},
        "102": {"display": "Child B", "parent_ids": ["900"]},
        "900": {"display": "Common Parent Term", "parent_ids": ["138875005"]} # 138875005 is a blocked root
    }
    
    # 2. Setup Mock Processed Document
    # We have one document that found both Child A and Child B
    doc = Document(
        document_id="d1",
        source_uri="gs://test/doc1.pdf",
        title="Test Document",
        publication_year=2024,
        doi="10.1234/test",
        evidence_level="Nivå 1"
    )
    
    finding_a = ProcessedFinding(
        finding_id="f1",
        document_id="d1",
        nursing_diagnosis="Child A",
        intervention="Int 1",
        goal="Goal 1",
        supporting_sentence_ids=["S1"],
        clinical_specificity=8,
        actionability_score=8,
        mapped_nursing_diagnosis=MappedTerm(term="Child A", ICNP_concept_id="101"),
        mapped_intervention=MappedTerm(term="Int 1", ICNP_concept_id=""),
        mapped_goal=MappedTerm(term="Goal 1", ICNP_concept_id=""),
        FO=FunctionalArea.FO3, # Respirasjon
        quotes=["Quote A"]
    )
    
    finding_b = ProcessedFinding(
        finding_id="f2",
        document_id="d1",
        nursing_diagnosis="Child B",
        intervention="Int 2",
        goal="Goal 2",
        supporting_sentence_ids=["S2"],
        clinical_specificity=8,
        actionability_score=8,
        mapped_nursing_diagnosis=MappedTerm(term="Child B", ICNP_concept_id="102"),
        mapped_intervention=MappedTerm(term="Int 2", ICNP_concept_id=""),
        mapped_goal=MappedTerm(term="Goal 2", ICNP_concept_id=""),
        FO=FunctionalArea.FO3, # Same FO
        quotes=["Quote B"]
    )
    
    processed_docs = [
        ProcessedDocument(source_document=doc, mapped_findings=[finding_a, finding_b])
    ]
    
    # 3. Execute Consolidation
    grouped_data = await group_findings(processed_docs)
    
    # 4. Assertions
    # We expect exactly ONE group because they merged under parent 900
    assert len(grouped_data) == 1, f"Expected 1 merged group, but got {len(grouped_data)}"
    
    group_key = f"{FunctionalArea.FO3}||900"
    assert group_key in grouped_data
    
    merged_finding = grouped_data[group_key]
    assert merged_finding["nursing_diagnosis"].ICNP_concept_id == "900"
    assert merged_finding["nursing_diagnosis"].term == "Common Parent Term"
    
    # Verify that evidence from both children was preserved
    evidence = merged_finding["supporting_evidence"]["d1"]
    assert "Quote A" in evidence["quotes"]
    assert "Quote B" in evidence["quotes"]
    
    # Verify that interventions were aggregated
    assert len(merged_finding["interventions"]) == 2
    
    print("\n✅ Consolidation Merge Test Passed: Successfully distilled two siblings into one parent.")

if __name__ == "__main__":
    import asyncio
    asyncio.run(test_sibling_merging_logic())
