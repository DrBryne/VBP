import os
from typing import List
from google.adk.agents import Agent, ParallelAgent
from google.genai import types
from app.shared.models import TermMappingResponse, FOClassificationResponse

def create_icnp_mapper():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "icnp_mapper.txt")
    terms_path = os.path.join(current_dir, "data", "restructured_terms.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        mapping_instructions = f.read()
    with open(terms_path, "r", encoding="utf-8") as f:
        icnp_terms = f.read()
    
    mapping_instructions = mapping_instructions.replace("{{icnp_terms}}", icnp_terms)

    config = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_level="high"),
    )

    return Agent(
        name="icnp_mapper",
        model="gemini-3.1-pro-preview",
        instruction=mapping_instructions,
        output_schema=TermMappingResponse,
        output_key="icnp_results",
        generate_content_config=config
    )

def create_fo_classifier():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    prompt_path = os.path.join(project_root, "app", "prompts", "fo_classifier.txt")

    with open(prompt_path, "r", encoding="utf-8") as f:
        fo_instructions = f.read()

    config = types.GenerateContentConfig(
        temperature=1.0,
        thinking_config=types.ThinkingConfig(include_thoughts=True, thinking_level="high"),
    )

    return Agent(
        name="fo_classifier",
        model="gemini-3.1-pro-preview",
        instruction=fo_instructions,
        output_schema=FOClassificationResponse,
        output_key="fo_results",
        generate_content_config=config
    )

def create_term_mapper():
    return ParallelAgent(
        name="term_mapper",
        sub_agents=[create_icnp_mapper(), create_fo_classifier()]
    )
