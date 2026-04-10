import json
import os
from collections.abc import AsyncGenerator

from google.adk.agents import Agent, BaseAgent, ParallelAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from app.shared.models import (
    DiagnosisMappingResponse,
    FunctionalAreaResponse,
    GoalMappingResponse,
    InterventionMappingResponse,
)
from app.shared.tools import load_prompt


def create_diagnosis_taxonomist():
    instructions = load_prompt("taxonomist_diagnosis")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    terms_path = os.path.join(current_dir, "data", "diagnoses.txt")
    with open(terms_path, encoding="utf-8") as f:
        icnp_terms = f.read()
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=10, http_status_codes=[429, 500, 502, 503, 504]),
            timeout=300000
        )
    )
    return Agent(
        name="diagnosis_taxonomist",
        model="gemini-3.1-pro-preview",
        instruction=instructions,
        output_schema=DiagnosisMappingResponse,
        output_key="diagnosis_mappings",
        generate_content_config=config
    )


def create_intervention_taxonomist():
    instructions = load_prompt("taxonomist_intervention")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    terms_path = os.path.join(current_dir, "data", "interventions.txt")
    with open(terms_path, encoding="utf-8") as f:
        icnp_terms = f.read()
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=10, http_status_codes=[429, 500, 502, 503, 504]),
            timeout=300000
        )
    )
    return Agent(
        name="intervention_taxonomist",
        model="gemini-3.1-pro-preview",
        instruction=instructions,
        output_schema=InterventionMappingResponse,
        output_key="intervention_mappings",
        generate_content_config=config
    )


def create_goal_taxonomist():
    instructions = load_prompt("taxonomist_goal")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    terms_path = os.path.join(current_dir, "data", "goals.txt")
    with open(terms_path, encoding="utf-8") as f:
        icnp_terms = f.read()
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=10, http_status_codes=[429, 500, 502, 503, 504]),
            timeout=300000
        )
    )
    return Agent(
        name="goal_taxonomist",
        model="gemini-3.1-pro-preview",
        instruction=instructions,
        output_schema=GoalMappingResponse,
        output_key="goal_mappings",
        generate_content_config=config
    )


def create_fo_classifier():
    instructions = load_prompt("fo_classifier")
    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=10, http_status_codes=[429, 500, 502, 503, 504]),
            timeout=300000
        )
    )
    return Agent(
        name="fo_classifier",
        model="gemini-3-flash-preview",
        instruction=instructions,
        output_schema=FunctionalAreaResponse,
        output_key="functional_areas",
        generate_content_config=config
    )


class ClinicalTaxonomist(BaseAgent):
    """
    A domain-specific agent that internally orchestrates Functional Area classification
    and ICNP mapping sequentially.
    """
    def __init__(self, name: str = "clinical_taxonomist"):
        super().__init__(name=name)
        self._fo_classifier = create_fo_classifier()
        self._icnp_mappers = ParallelAgent(
            name="icnp_mappers",
            sub_agents=[
                create_diagnosis_taxonomist(),
                create_intervention_taxonomist(),
                create_goal_taxonomist(),
            ]
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # The raw findings list is in the latest user event content parts
        # Following ADK 2.0 convention, we assume the latest event contains the JSON payload.
        findings_json = None
        for part in ctx.session.events[-1].content.parts:
            if part.text:
                findings_json = part.text
                break

        if not findings_json:
            return

        # Step 1: Functional Area Classification
        async for ev in self._fo_classifier.run_async(ctx):
            yield ev
            if ev.is_final_response():
                # Store in session state for internal use
                from app.shared.processing import safe_parse_json
                data_dict = safe_parse_json(ev)
                if data_dict:
                    ctx.session.state["functional_areas"] = FunctionalAreaResponse.model_validate(data_dict)

        functional_areas: FunctionalAreaResponse = ctx.session.state.get("functional_areas")
        if not functional_areas:
            return

        # Step 2: FO-Guided ICNP Mapping
        fo_lookup = {res.finding_id: res.FO for res in functional_areas.results}
        raw_findings = json.loads(findings_json) # Simplistic parse, assuming first user message was just the list

        # Build enriched payload
        guided_findings = []
        for rf in raw_findings:
            f_id = rf.get("finding_id")
            guided_findings.append({
                **rf,
                "assigned_FO": fo_lookup.get(f_id, "Unknown")
            })

        # Construct new context message for mappers
        mapper_msg = types.Content(role="user", parts=[
            types.Part.from_text(text="Map these findings to ICNP using the assigned_FO:"),
            types.Part.from_text(text=json.dumps(guided_findings))
        ])

        # Inject the enriched context into the session
        ctx.session.events.append(Event(author="system", content=mapper_msg))

        # Invoke mappers
        async for ev in self._icnp_mappers.run_async(ctx):
            yield ev
