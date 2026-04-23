import pytest
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
async def test_refset_and_depth_gatekeeper():
    """
    1. Tests that Refset terms merge even if shallow (Depth 1).
    2. Tests that non-Refset terms merge ONLY if deep (Depth 5+).
    3. Tests that shallow non-Refset terms (Depth 4) are BLOCKED.
    """
    taxonomy_cache["concepts"].clear()
    norwegian_refset_ids.clear()

    # 1. Setup Mock Taxonomy Cache
    taxonomy_cache["concepts"] = {
        # Pair 1: Shallow Refset terms -> SHOULD MERGE
        "101": {"display": "Ref Child 1", "parent_ids": ["100"]},
        "102": {"display": "Ref Child 2", "parent_ids": ["100"]},
        "100": {"display": "Ref Parent", "parent_ids": ["138875005"]}, # Depth 1
        
        # Pair 2: Deep Non-Refset terms -> SHOULD MERGE (Depth 5)
        "201": {"display": "Deep 1", "parent_ids": ["200"]},
        "202": {"display": "Deep 2", "parent_ids": ["200"]},
        "200": {"display": "Deep Parent", "parent_ids": ["2004"]},
        "2004": {"display": "D4", "parent_ids": ["2003"]},
        "2003": {"display": "D3", "parent_ids": ["2002"]},
        "2002": {"display": "D2", "parent_ids": ["2001"]},
        "2001": {"display": "D1", "parent_ids": ["138875005"]},

        # Pair 3: Shallow Non-Refset terms -> SHOULD NOT MERGE (Depth 4)
        "301": {"display": "Shallow 1", "parent_ids": ["300"]},
        "302": {"display": "Shallow 2", "parent_ids": ["300"]},
        "300": {"display": "Shallow Parent", "parent_ids": ["3003"]},
        "3003": {"display": "S3", "parent_ids": ["3002"]},
        "3002": {"display": "S2", "parent_ids": ["3001"]},
        "3001": {"display": "S1", "parent_ids": ["138875005"]}
    }

    # 2. Setup Refset
    norwegian_refset_ids.clear()
    norwegian_refset_ids.update({"101", "102", "100"})

    # 3. Setup Mock Document
    doc = Document(document_id="d1", source_uri="gs://test/1.pdf", title="T", evidence_level="Nivå 1", publication_year=2024, doi="10.1")

    def create_f(fid, cid, fo):
        return ProcessedFinding(
            finding_id=fid, document_id="d1", nursing_diagnosis="Diag", intervention="Int", goal="Goal", 
            supporting_sentence_ids=["S1"], clinical_specificity=7, actionability_score=8,
            mapped_nursing_diagnosis=MappedTerm(term="Diag", ICNP_concept_id=cid),
            mapped_intervention=MappedTerm(term="Int", ICNP_concept_id=""),
            mapped_goal=MappedTerm(term="Goal", ICNP_concept_id=""),
            FO=fo, quotes=["Q"],
            auditor_rating=AuditorRating(finding_id=fid, specificity_score=7, actionability_score=8, cohesion_score=9, auditor_comment="G")
        )

    findings = [
        create_f("f1", "101", FunctionalArea.FO1),
        create_f("f2", "102", FunctionalArea.FO1),
        create_f("f3", "201", FunctionalArea.FO2),
        create_f("f4", "202", FunctionalArea.FO2),
        create_f("f5", "301", FunctionalArea.FO3),
        create_f("f6", "302", FunctionalArea.FO3)
    ]

    processed_docs = [ProcessedDocument(source_document=doc, mapped_findings=findings)]

    # 4. Execute
    grouped_data = await group_findings(processed_docs, fhir_client=None)

    # 5. Assertions
    
    # FO1: Refset terms SHOULD merge (Depth 1 is ignored for Refset)
    fo1_groups = [k for k in grouped_data.keys() if k.startswith(str(FunctionalArea.FO1))]
    assert len(fo1_groups) == 1, f"Expected 1 group, got {fo1_groups}"
    assert "100" in fo1_groups[0]

    # FO2: Deep Non-Refset terms SHOULD merge (Depth 5)
    fo2_groups = [k for k in grouped_data.keys() if k.startswith(str(FunctionalArea.FO2))]
    assert len(fo2_groups) == 1, f"Expected 1 group, got {fo2_groups}"
    assert "200" in fo2_groups[0]

    # FO3: Shallow Non-Refset terms SHOULD NOT merge (Depth 4 < 5)
    fo3_groups = [k for k in grouped_data.keys() if k.startswith(str(FunctionalArea.FO3))]
    assert len(fo3_groups) == 2, f"Expected 2 separate groups for shallow parents, got {fo3_groups}"

    print("\n✅ Refset and Depth Gatekeeper Test Passed: Logic correctly respects Refset priority and Depth thresholds.")
