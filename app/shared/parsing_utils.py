import json
from typing import Any

from google.adk.events import Event

from app.shared.logging import VBPLogger

logger = VBPLogger("vbp_parsing")

def safe_parse_json(event: Event) -> dict[str, Any] | None:
    """
    Safely extracts and parses JSON from an ADK Event.

    Uses strict guards for content and parts to prevent AttributeError
    if an agent returns an empty or safety-blocked response.

    Args:
        event: The final response event from an ADK Agent.

    Returns:
        The parsed dictionary or None if parsing/validation fails.
    """
    if not event.content or not event.content.parts or not event.content.parts[0].text:
        return None
    try:
        text = event.content.parts[0].text.strip()
        # Handle cases where LLM wraps JSON in markdown code blocks
        if text.startswith("```json"):
            text = text.split("```json")[1].split("```")[0].strip()
        elif text.startswith("```"):
            text = text.split("```")[1].split("```")[0].strip()

        return json.loads(text)
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        logger.error(f"Failed to parse LLM response: {e}")
        return None
