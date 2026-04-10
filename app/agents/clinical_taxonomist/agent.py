import os

from google.adk.agents import Agent, ParallelAgent
from google.genai import types

from app.shared.models import FunctionalAreaResponse, IcnpMappingResponse
from app.shared.tools import load_prompt


def create_icnp_mapper():
    instructions = load_prompt("clinical_taxonomist")

    current_dir = os.path.dirname(os.path.abspath(__file__))
    terms_path = os.path.join(current_dir, "data", "restructured_terms.txt")
    with open(terms_path, encoding="utf-8") as f:
        icnp_terms = f.read()

    instructions = instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=2,
                max_delay=60,
                exp_base=2.0,
                jitter=True,
                attempts=10,
                http_status_codes=[429, 500, 502, 503, 504]
            ),
            timeout=300000
        )
    )

    return Agent(
        name="icnp_mapper",
        model="gemini-3.1-pro-preview",
        instruction=instructions,
        output_schema=IcnpMappingResponse,
        output_key="icnp_mappings",
        generate_content_config=config
    )

def create_fo_classifier():
    instructions = load_prompt("fo_classifier")

    config = types.GenerateContentConfig(
        temperature=1.0,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=2,
                max_delay=60,
                exp_base=2.0,
                jitter=True,
                attempts=10,
                http_status_codes=[429, 500, 502, 503, 504]
            ),
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

def create_combined_taxonomist():
    return ParallelAgent(
        name="combined_taxonomist",
        sub_agents=[create_icnp_mapper(), create_fo_classifier()]
    )
