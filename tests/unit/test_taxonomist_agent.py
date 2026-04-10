import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.clinical_taxonomist.agent import (
    create_fo_classifier,
    create_icnp_mappers,
)
from app.shared.models import (
    DiagnosisMappingResponse,
    FunctionalAreaResponse,
    GoalMappingResponse,
    InterventionMappingResponse,
)
from app.shared.processing import load_valid_icnp_ids, safe_parse_json

# A selection of challenging, highly specific raw findings from the VBP run
TEST_FINDINGS = [
    {
        "finding_id": "test_1",
        "nursing_diagnosis": "Risiko for overbelastningsskade og forlenget tap av muskelstyrke i svekket eller denervet muskulatur.",
        "intervention": "Sikre at treningsprogrammet holdes på et lavt til moderat intensitetsnivå.",
        "goal": "Unngå iatrogen muskelskade og utmattelse (fatigue)."
    },
    {
        "finding_id": "test_2",
        "nursing_diagnosis": "Svelgevansker med økt risiko for aspirasjon og feilernæring.",
        "intervention": "Henvisning til logoped for vurdering av svelgefunksjon og tilpasning av konsistens.",
        "goal": "Sikker oral inntak uten tegn til aspirasjonspneumoni."
    },
    {
        "finding_id": "test_3",
        "nursing_diagnosis": "Risiko for at pasientens autonomi krenkes på grunn av alvorlig kommunikasjonssvikt (f.eks. locked-in syndrom).",
        "intervention": "Benytte alternative kommunikasjonsmetoder (ASK) og etablere forhåndssamtaler (ACP).",
        "goal": "Pasientens behandlingspreferanser forblir tydelig dokumentert."
    },
    {
        "finding_id": "test_4",
        "nursing_diagnosis": "Respirasjonssvikt type 2 (hyperkapnisk) grunnet svakhet i respirasjonsmuskulatur.",
        "intervention": "Oppstart og tilpasning av non-invasiv ventilasjon (NIV) med BIPAP.",
        "goal": "Normalisering av pCO2 og lindring av dyspné."
    }
]

@pytest.mark.asyncio
async def test_taxonomist_mapping_accuracy():
    """
    Tests that the ClinicalTaxonomist can successfully map complex, 
    disease-specific raw findings to generic, valid ICNP Concept IDs.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

    assert project_id is not None, "GOOGLE_CLOUD_PROJECT must be set."

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test_app", user_id="test_user", session_id="test_session")
    import uuid
    fo_agent = create_fo_classifier()
    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=fo_agent,
        run_config=RunConfig()
    )

    # Prepare the input payload
    mapper_msg = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="Map these findings to ICNP and classify FO:"),
            types.Part.from_text(text=json.dumps(TEST_FINDINGS))
        ]
    )
    session.events.append(Event(author="system", content=mapper_msg))

    # Run Step 1: FO Classifier
    print(f"\n[Step 1] Classifying Functional Areas for {len(TEST_FINDINGS)} findings...")

    functional_areas = None
    async for ev in fo_agent.run_async(ctx):
        if ev.is_final_response():
            data_dict = safe_parse_json(ev)
            if data_dict:
                functional_areas = FunctionalAreaResponse.model_validate(data_dict)

    assert functional_areas is not None, "FO classifier failed."
    fo_lookup = {res.finding_id: res.FO for res in functional_areas.results}

    # Run Step 2: FO-Guided ICNP Mappers
    print("\n[Step 2] Sending FO-guided findings to specialized ICNP Mappers...")
    icnp_mappers = create_icnp_mappers()
    ctx_mappers = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=icnp_mappers,
        run_config=RunConfig()
    )

    guided_findings = []
    for lf in TEST_FINDINGS:
        guided_findings.append({
            **lf,
            "assigned_FO": fo_lookup.get(lf["finding_id"], "Unknown"),
            "context_trace": "Simulated reasoning trace context for TDD."
        })

    # Re-prepare payload for mappers
    mapper_msg = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="Map these findings to ICNP:"),
            types.Part.from_text(text=json.dumps(guided_findings))
        ]
    )
    session.events.append(Event(author="system", content=mapper_msg))

    icnp_diag_mappings = None
    icnp_int_mappings = None
    icnp_goal_mappings = None

    async for ev in icnp_mappers.run_async(ctx_mappers):
        if ev.is_final_response():
            data_dict = safe_parse_json(ev)
            if not data_dict:
                print(f"\n[ERROR] Failed to parse JSON from {ev.author}. Raw output:\n{ev.content.parts[0].text if ev.content and ev.content.parts else 'Empty'}")
                continue

            if ev.author == "diagnosis_taxonomist":
                icnp_diag_mappings = DiagnosisMappingResponse.model_validate(data_dict)
            elif ev.author == "intervention_taxonomist":
                icnp_int_mappings = InterventionMappingResponse.model_validate(data_dict)
            elif ev.author == "goal_taxonomist":
                icnp_goal_mappings = GoalMappingResponse.model_validate(data_dict)

    # 1. Assert Responses Exist
    assert icnp_diag_mappings is not None, "DiagnosisTaxonomist failed."
    assert icnp_int_mappings is not None, "InterventionTaxonomist failed."
    assert icnp_goal_mappings is not None, "GoalTaxonomist failed."

    # 2. Assert ICNP IDs are VALID
    valid_icnp_ids = load_valid_icnp_ids()

    # Stitch results for assertion
    diag_lookup = {res.finding_id: res.nursing_diagnosis for res in icnp_diag_mappings.results}
    int_lookup = {res.finding_id: res.intervention for res in icnp_int_mappings.results}

    success_count = 0
    for finding in TEST_FINDINGS:
        f_id = finding["finding_id"]
        diag = diag_lookup.get(f_id)
        interv = int_lookup.get(f_id)

        diag_id = diag.ICNP_concept_id if diag else ""
        interv_id = interv.ICNP_concept_id if interv else ""

        print(f"\nFinding ID: {f_id}")
        print(f"  Diagnosis Mapped ID: '{diag_id}'")
        print(f"  Intervention Mapped ID: '{interv_id}'")

        assert diag_id != "", f"Failed to map diagnosis for {f_id}"
        assert diag_id in valid_icnp_ids, f"Hallucinated diag ID for {f_id}"
        success_count += 1
    print(f"\n✅ SUCCESS: {success_count}/{len(TEST_FINDINGS)} findings successfully mapped to valid ICNP IDs.")
