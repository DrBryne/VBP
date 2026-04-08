import os
from google.adk.agents import Agent
from google.genai import types
from app.shared.models import SynthesisResponse

def create_consolidator():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "consolidator.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        system_instructions = f.read()

    generate_content_config = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_level="high"
        )
    )

    return Agent(
        name="consolidator",
        model="gemini-3.1-pro-preview",
        instruction=system_instructions,
        output_schema=SynthesisResponse,
        generate_content_config=generate_content_config
    )
