from google.adk.agents import Agent, ParallelAgent
from google.genai import types

from app.shared.models import ClinicalFindingsResponse, MetadataResponse
from app.shared.tools import load_prompt


def create_metadata_extractor():
    instructions = load_prompt("metadata_extractor")

    config = types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=65536,
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
        name="metadata_extractor",
        model="gemini-3-flash-preview",
        instruction=instructions,
        output_schema=MetadataResponse,
        output_key="metadata",
        generate_content_config=config
    )

def create_clinical_extractor():
    instructions = load_prompt("clinical_extractor")

    config = types.GenerateContentConfig(
        temperature=1.0,
        max_output_tokens=65536,
        thinking_config=types.ThinkingConfig(
            thinking_level="high"
        ),
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
        name="clinical_extractor",
        model="gemini-3.1-pro-preview",
        instruction=instructions,
        output_schema=ClinicalFindingsResponse,
        output_key="clinical_findings",
        generate_content_config=config
    )

def create_combined_extractor():
    """Returns a ParallelAgent combining Metadata and Clinical extraction."""
    return ParallelAgent(
        name="combined_extractor",
        sub_agents=[
            create_metadata_extractor(),
            create_clinical_extractor()
        ]
    )
