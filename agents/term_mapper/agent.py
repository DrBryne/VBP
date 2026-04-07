import os
import json
import asyncio
import uuid
from typing import Optional, Tuple
from dotenv import load_dotenv
from google.genai import types
from google.adk.agents import Agent
from vertexai.agent_engines import AdkApp

# Load environment variables from .env file
load_dotenv()

import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from shared.models import (
    ResponseSchema,
    MappedResponseSchema, 
    SimplifiedFinding, 
    LLMMappingResponse, 
    LLMFOClassificationResponse,
    MappedFinding,
    NursingDiagnosisMapping,
    InterventionMapping,
    GoalMapping
)

def get_term_mapper_agents() -> Tuple[Agent, Agent]:
    """Returns the configured ADK Agents for ICNP mapping and FO classification."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    mapping_prompt_path = os.path.join(current_dir, "prompt.txt")
    fo_prompt_path = os.path.join(current_dir, "fo_prompt.txt")
    terms_path = os.path.join(current_dir, "restructured_terms.txt")

    try:
        with open(mapping_prompt_path, "r", encoding="utf-8") as f:
            mapping_instructions = f.read()
        with open(fo_prompt_path, "r", encoding="utf-8") as f:
            fo_instructions = f.read()
        with open(terms_path, "r", encoding="utf-8") as f:
            icnp_terms = f.read()
        mapping_instructions = mapping_instructions.replace("{{icnp_terms}}", icnp_terms)
    except FileNotFoundError as e:
        print(f"Error loading required files: {e}")
        raise e

    config_base = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_level="high"),
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(
                initial_delay=1,
                attempts=10,
                http_status_codes=[429, 500, 502, 503, 504]
            ),
            timeout=300000
        )
    )

    mapping_agent = Agent(
        name="icnp_mapper",
        model="gemini-3.1-pro-preview",
        instruction=mapping_instructions,
        output_schema=LLMMappingResponse.model_json_schema(),
        generate_content_config=config_base
    )

    fo_agent = Agent(
        name="fo_classifier",
        model="gemini-3.1-pro-preview",
        instruction=fo_instructions,
        output_schema=LLMFOClassificationResponse.model_json_schema(),
        generate_content_config=config_base
    )

    return mapping_agent, fo_agent

async def call_gemini_app(app: AdkApp, input_json: str, schema):
    """Helper to call a Gemini app and parse the JSON response natively."""
    responses = app.async_stream_query(
        user_id=str(uuid.uuid4()),
        message={
            "role": "user",
            "parts": [
                types.Part.from_text(text=f"Prosesser disse dataene:\n\n{input_json}")
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
    
    # ADK 2.0 ensures output matches schema
    text_to_parse = final_text.strip()
    if text_to_parse.startswith("```json"):
        text_to_parse = text_to_parse[7:]
    elif text_to_parse.startswith("```"):
        text_to_parse = text_to_parse[3:]
    if text_to_parse.endswith("```"):
        text_to_parse = text_to_parse[:-3]
    text_to_parse = text_to_parse.strip()
    
    try:
        raw_results = json.loads(text_to_parse)
        return schema.model_validate(raw_results)
    except Exception as e:
        print(f"Error parsing app response: {e}")
        return None

async def map_terms_with_apps(mapping_app: AdkApp, fo_app: AdkApp, input_data: ResponseSchema) -> Optional[MappedResponseSchema]:
    """
    Executes mapping and classification via AdkApps.
    """
    simplified_findings = []
    finding_map = {}
    for finding in input_data.Candidate_findings:
        internal_id = str(uuid.uuid4())
        simplified = SimplifiedFinding(
            finding_id=internal_id,
            nursing_diagnosis=finding.nursing_diagnosis,
            intervention=finding.intervention,
            goal=finding.goal
        )
        simplified_findings.append(simplified)
        finding_map[internal_id] = finding

    input_json = json.dumps([sf.model_dump() for sf in simplified_findings])

    # Run in parallel
    mapping_task = call_gemini_app(mapping_app, input_json, LLMMappingResponse)
    fo_task = call_gemini_app(fo_app, input_json, LLMFOClassificationResponse)
    
    mapping_response, fo_response = await asyncio.gather(mapping_task, fo_task)

    if not mapping_response or not fo_response:
        print("One of the app calls failed.")
        return None

    # Post-process and merge
    mapping_results = {res.finding_id: res for res in mapping_response.results}
    fo_results = {res.finding_id: res.FO for res in fo_response.results}
    
    mapped_findings = []
    for sf in simplified_findings:
        original = finding_map[sf.finding_id]
        map_res = mapping_results.get(sf.finding_id)
        fo_val = fo_results.get(sf.finding_id, "12. Annet/legedelegerte aktiviteter")
        
        def resolve(orig_val, mapping, cls):
            if mapping and mapping.term:
                return cls(term=mapping.term, ICNP_concept_id=mapping.ICNP_concept_id)
            return cls(term=orig_val, ICNP_concept_id="")

        mapped_finding = MappedFinding(
            nursing_diagnosis=resolve(original.nursing_diagnosis, map_res.nursing_diagnosis if map_res else None, NursingDiagnosisMapping),
            intervention=resolve(original.intervention, map_res.intervention if map_res else None, InterventionMapping),
            goal=resolve(original.goal, map_res.goal if map_res else None, GoalMapping),
            FO=fo_val,
            quotes=original.quotes,
            document_id=original.document_id
        )
        mapped_findings.append(mapped_finding)

    return MappedResponseSchema(
        source_document=input_data.source_document,
        Candidate_findings=mapped_findings
    )
