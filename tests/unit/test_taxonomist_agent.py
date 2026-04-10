import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.clinical_taxonomist.agent import create_combined_taxonomist
from app.shared.models import FunctionalAreaResponse, IcnpMappingResponse
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

    taxonomist = create_combined_taxonomist()
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test_app", user_id="test_user", session_id="test_session")
    import uuid
    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=taxonomist,
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

    # Run the agent
    print(f"\nSending {len(TEST_FINDINGS)} complex findings to ClinicalTaxonomist...")

    icnp_mappings = None
    functional_areas = None

    async for ev in taxonomist.run_async(ctx):
        if ev.is_final_response():
            data_dict = safe_parse_json(ev)
            if not data_dict:
                print(f"\n[ERROR] Failed to parse JSON from {ev.author}. Raw output:\n{ev.content.parts[0].text if ev.content and ev.content.parts else 'Empty'}")
                continue

            if ev.author == "icnp_mapper":
                icnp_mappings = IcnpMappingResponse.model_validate(data_dict)
            elif ev.author == "fo_classifier":
                functional_areas = FunctionalAreaResponse.model_validate(data_dict)

    # 1. Assert Responses Exist
    assert icnp_mappings is not None, "Taxonomist failed to return ICNP mappings."
    assert functional_areas is not None, "Taxonomist failed to return FO classifications."
    assert len(icnp_mappings.results) == len(TEST_FINDINGS), "Did not return a mapping for every finding."

    # 2. Assert ICNP IDs are VALID (This is the bug we are fixing)
    valid_icnp_ids = load_valid_icnp_ids()

    success_count = 0
    for result in icnp_mappings.results:
        diag_id = result.nursing_diagnosis.ICNP_concept_id if result.nursing_diagnosis else ""
        interv_id = result.intervention.ICNP_concept_id if result.intervention else ""

        print(f"\nFinding ID: {result.finding_id}")
        print(f"  Diagnosis Mapped ID: '{diag_id}'")
        print(f"  Intervention Mapped ID: '{interv_id}'")

        # The core assertion: The LLM must not return an empty ID or a hallucinated ID for the diagnosis
        assert diag_id != "", f"Failed to map a diagnosis for finding {result.finding_id}"
        assert diag_id in valid_icnp_ids, f"Hallucinated Diagnosis ID: {diag_id} for finding {result.finding_id}"

        success_count += 1

    print(f"\n✅ SUCCESS: {success_count}/{len(TEST_FINDINGS)} findings successfully mapped to valid ICNP IDs.")
