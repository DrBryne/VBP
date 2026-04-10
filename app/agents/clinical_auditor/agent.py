import os
from google.adk.agents import Agent
from google.genai import types
from app.shared.models import AuditorResponse
from app.shared.tools import load_prompt

def create_clinical_auditor():
    """
    Creates the Clinical Auditor agent responsible for multi-dimensional quality scoring.
    """
    instructions = load_prompt("clinical_auditor")

    config = types.GenerateContentConfig(
        temperature=0.0,  # Highly deterministic for audit consistency
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
        name="clinical_auditor",
        model="gemini-3-flash-preview",
        instruction=instructions,
        output_schema=AuditorResponse,
        output_key="auditor_results",
        generate_content_config=config
    )
