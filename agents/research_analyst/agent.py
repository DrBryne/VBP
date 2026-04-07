import os
import mimetypes
import uuid
import asyncio
from typing import Optional
from google.genai import types
from google.adk.agents import Agent
from vertexai.agent_engines import AdkApp

import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.models import ModelSchema, SourceDocumentEnriched, FindingEnriched, ResponseSchema

def get_research_analyst_agent() -> Agent:
    """Returns the configured ADK Agent for research analysis."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    prompt_path = os.path.join(current_dir, "prompt.txt")

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
        output_schema=ModelSchema.model_json_schema(),
        generate_content_config=generate_content_config
    )

async def analyze_document_with_app(app: AdkApp, target_group: str, gcs_uri: str) -> Optional[ResponseSchema]:
    """Helper to execute the agent via AdkApp and enrich the results."""
    mime_type, _ = mimetypes.guess_type(gcs_uri)
    if mime_type == 'application/xml' or gcs_uri.lower().endswith('.xml'):
        mime_type = 'text/plain'
    elif not mime_type:
        if gcs_uri.lower().endswith('.pdf'): mime_type = 'application/pdf'
        elif gcs_uri.lower().endswith('.txt'): mime_type = 'text/plain'
        else: mime_type = 'application/octet-stream'

    file_part = types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type)
    
    # We pass the target group in the user message instead of appending to system instructions
    message_text = f"Bruksområde: {target_group}\n\nAnalyser den vedlagte artikkelen og trekk ut funn og informasjon om kildedokumentet som JSON i henhold til instruksjonene."
    
    responses = app.async_stream_query(
        user_id=str(uuid.uuid4()),
        message={
            "role": "user",
            "parts": [
                file_part,
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
        print(f"Empty response from agent for document {gcs_uri}")
        return None
        
    try:
        # ADK 2.0 ensures the output matches the schema, we can parse it directly
        import json
        text_to_parse = final_text.strip()
        if text_to_parse.startswith("```json"):
            text_to_parse = text_to_parse[7:]
        elif text_to_parse.startswith("```"):
            text_to_parse = text_to_parse[3:]
        if text_to_parse.endswith("```"):
            text_to_parse = text_to_parse[:-3]
        
        raw_data = json.loads(text_to_parse.strip())
        model_data = ModelSchema.model_validate(raw_data)
        
        doc_id = str(uuid.uuid4())
        enriched_source_document = SourceDocumentEnriched(
            **model_data.source_document.model_dump(),
            document_id=doc_id
        )
        enriched_findings = [
            FindingEnriched(**finding.model_dump(), document_id=doc_id)
            for finding in model_data.Candidate_findings
        ]
        
        return ResponseSchema(
            source_document=enriched_source_document,
            Candidate_findings=enriched_findings
        )
        
    except Exception as e:
        print(f"Error analyzing document {gcs_uri}: {e}")
        return None
