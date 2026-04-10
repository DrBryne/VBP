import json
import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.clinical_taxonomist.agent import ClinicalTaxonomist
from app.shared.models import (
    DiagnosisMappingResponse,
    FunctionalAreaResponse,
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
    }
]

@pytest.mark.asyncio
async def test_taxonomist_encapsulation():
    """
    Tests that the ClinicalTaxonomist correctly orchestrates its internal
    sequential FO classification and ICNP mapping flow.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

    taxonomist = ClinicalTaxonomist()
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="test_app", user_id="test_user", session_id="test_session")

    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=taxonomist,
        run_config=RunConfig()
    )

    # Prepare the input payload (the raw list of findings)
    mapper_msg = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text=json.dumps(TEST_FINDINGS))
        ]
    )
    session.events.append(Event(author="system", content=mapper_msg))

    print(f"\nRunning encapsulated ClinicalTaxonomist for {len(TEST_FINDINGS)} findings...")

    icnp_diag_mappings = None
    functional_areas = None

    async for ev in taxonomist.run_async(ctx):
        if ev.is_final_response():
            data_dict = safe_parse_json(ev)
            if not data_dict: continue

            if ev.author == "diagnosis_taxonomist":
                icnp_diag_mappings = DiagnosisMappingResponse.model_validate(data_dict)
            elif ev.author == "fo_classifier":
                functional_areas = FunctionalAreaResponse.model_validate(data_dict)

    # 1. Assert Results reached the session state via the internal orchestration
    assert functional_areas is not None, "Internal FO classification failed."
    assert icnp_diag_mappings is not None, "Internal ICNP mapping failed."

    # 2. Verify validity
    valid_icnp_ids = load_valid_icnp_ids()
    for res in icnp_diag_mappings.results:
        diag_id = res.nursing_diagnosis.ICNP_concept_id if res.nursing_diagnosis else ""
        print(f"Finding {res.finding_id} -> Mapped ID: {diag_id}")
        assert diag_id in valid_icnp_ids

    print("\n✅ SUCCESS: ClinicalTaxonomist correctly orchestrated its internal sub-agents.")
