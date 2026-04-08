import os
from google.adk.agents import Agent
from google.genai import types
from app.shared.models import ExtractionResponse

def create_research_analyst():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "research_analyst.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        system_instructions = f.read()

    generate_content_config = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_level="high"
        ),
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1,
                attempts=10,
                http_status_codes=[429, 500, 502, 503, 504]
            ),
            timeout=300000
        )
    )

    return Agent(
        name="research_analyst",
        model="gemini-3.1-pro-preview",
        instruction=system_instructions,
        output_schema=ExtractionResponse,
        output_key="analyst_results",
        generate_content_config=generate_content_config
    )
