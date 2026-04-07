import os
import json
import uuid
from typing import Optional
from google.genai import types
from google.adk.agents import Agent
from vertexai.agent_engines import AdkApp

# --- Pydantic Schemas ---
import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.models import ConsolidatedResponseSchema, SynthesisSchema

def get_consolidator_agent() -> Agent:
    """Returns the configured ADK Agent for synthesizing findings."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(current_dir, "prompt.txt")

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_instructions = f.read()
    except FileNotFoundError as e:
        print(f"Error loading prompt: {e}")
        raise e

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
        name="consolidator",
        model="gemini-3.1-pro-preview",
        instruction=system_instructions,
        output_schema=SynthesisSchema.model_json_schema(),
        generate_content_config=generate_content_config
    )

async def consolidate_findings_with_app(app: AdkApp, target_group: str, input_data: ConsolidatedResponseSchema) -> Optional[SynthesisSchema]:
    """
    Consolidates multiple mapped responses into a single, synthesized output via AdkApp.
    """
    # Convert the consolidated data into a simplified JSON for the LLM
    input_json = input_data.model_dump_json()

    # Pass the target group context in the user message instead of appending to system instructions
    message_text = f"Målgruppe: {target_group}\n\nVennligst syntetiser disse kliniske funnene:\n\n{input_json}"

    responses = app.async_stream_query(
        user_id=str(uuid.uuid4()),
        message={
            "role": "user",
            "parts": [
                types.Part.from_text(text=message_text)
            ]
        }
    )
    
    final_text = ""
    async for chunk in responses:
        if isinstance(chunk, dict) and "content" in chunk:
            parts = chunk["content"].get("parts", [])
            for p in parts:
                if p.get("thought") is not True:
                    final_text += p.get("text", "")
        elif hasattr(chunk, "text") and chunk.text:
            if not getattr(chunk, "thought", False):
                final_text += chunk.text

    if not final_text.strip():
        print(f"Empty response from consolidator agent.")
        return None
        
    try:
        # ADK 2.0 ensures the output matches the schema
        text_to_parse = final_text.strip()
        if text_to_parse.startswith("```json"):
            text_to_parse = text_to_parse[7:]
        elif text_to_parse.startswith("```"):
            text_to_parse = text_to_parse[3:]
        if text_to_parse.endswith("```"):
            text_to_parse = text_to_parse[:-3]
        
        raw_data = json.loads(text_to_parse.strip())
        return SynthesisSchema.model_validate(raw_data)
        
    except Exception as e:
        print(f"Error consolidating findings: {e}")
        return None
