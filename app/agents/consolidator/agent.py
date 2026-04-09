import os
from google.adk.agents import Agent
from google.genai import types

from app.shared.models import EvidenceValidationResponse

def create_evidence_validator():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "evidence_validator.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        instructions = f.read()

    config = types.GenerateContentConfig(
        temperature=0.0, # Strict deterministic-like logic
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
        name="evidence_validator",
        model="gemini-3-flash-preview",
        instruction=instructions,
        output_schema=EvidenceValidationResponse,
        generate_content_config=config
    )

def create_quality_evaluator():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "quality_evaluator.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        instructions = f.read()

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
        name="quality_evaluator",
        model="gemini-3-flash-preview",
        instruction=instructions,
        generate_content_config=config
    )
