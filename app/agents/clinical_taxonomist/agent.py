import json
import os
from collections.abc import AsyncGenerator

from google.adk.agents import Agent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types

from app.app_utils.telemetry import track_telemetry_span
from app.shared.models import (
    DiagnosisMappingResponse,
    FunctionalAreaResponse,
    GoalMappingResponse,
    InterventionMappingResponse,
)
from app.shared.tools import load_prompt

# --- GLOBAL TERMINOLOGY CACHE ---
# Load massive dictionary files once per container lifecycle to prevent OOM
# and reduce string duplication during high-concurrency runs.
# Because this is at the module level, it is shared by all sub-agents.
_TERMINOLOGY_CACHE = {}

def _get_cached_terms(filename: str) -> str:
    if filename not in _TERMINOLOGY_CACHE:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        terms_path = os.path.join(current_dir, "data", filename)
        with open(terms_path, encoding="utf-8") as f:
            _TERMINOLOGY_CACHE[filename] = f.read()
    return _TERMINOLOGY_CACHE[filename]


def create_diagnosis_taxonomist():
    instructions = load_prompt("taxonomist_diagnosis")
    icnp_terms = _get_cached_terms("diagnoses.txt")
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=65536,
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
    icnp_terms = _get_cached_terms("interventions.txt")
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=65536,
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
    icnp_terms = _get_cached_terms("goals.txt")
    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=65536,
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
        max_output_tokens=65536,
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
    and ICNP mapping sequentially. Runs specialized mappers safely in parallel so a
    single failure (e.g. timeout) does not crash the entire taxonomy step.
    """
    def __init__(self, name: str = "clinical_taxonomist"):
        super().__init__(name=name)
        self._fo_classifier = create_fo_classifier()
        self._diagnosis_taxonomist = create_diagnosis_taxonomist()
        self._intervention_taxonomist = create_intervention_taxonomist()
        self._goal_taxonomist = create_goal_taxonomist()

    @track_telemetry_span("Agent: ClinicalTaxonomist Mapping")
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        # The raw findings list and reasoning trace are in the latest user event content parts
        findings_json = None
        reasoning_trace = "No context provided."

        for part in ctx.session.events[-1].content.parts:
            if part.text:
                if part.text.startswith("["):
                    findings_json = part.text
                elif "Reasoning Context:" in part.text:
                    reasoning_trace = part.text

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
        raw_findings = json.loads(findings_json)

        # Build enriched payload
        guided_findings = []
        for rf in raw_findings:
            f_id = rf.get("finding_id")
            guided_findings.append({
                **rf,
                "assigned_FO": fo_lookup.get(f_id, "Unknown"),
                "context_trace": reasoning_trace
            })

        # Construct new context message for mappers
        mapper_msg = types.Content(role="user", parts=[
            types.Part.from_text(text="Map these findings to ICNP. Use the assigned_FO and context_trace for better accuracy:"),
            types.Part.from_text(text=json.dumps(guided_findings))
        ])

        # Inject the enriched context into the session
        ctx.session.events.append(Event(author="system", content=mapper_msg))

        # Safely run mappers concurrently
        import asyncio

        from google.adk.agents.parallel_agent import _create_branch_ctx_for_sub_agent

        from app.shared.logging import VBPLogger
        local_logger = VBPLogger("taxonomist_mappers")

        async def run_safe(agent: BaseAgent):
            events = []
            try:
                sub_ctx = _create_branch_ctx_for_sub_agent(self, agent, ctx)
                async for ev in agent.run_async(sub_ctx):
                    events.append(ev)
            except Exception as e:
                local_logger.error(f"Mapper {agent.name} failed: {e}")
            return events

        # Gather results safely without crashing the whole process
        results = await asyncio.gather(
            run_safe(self._diagnosis_taxonomist),
            run_safe(self._intervention_taxonomist),
            run_safe(self._goal_taxonomist)
        )

        for events in results:
            for ev in events:
                yield ev
