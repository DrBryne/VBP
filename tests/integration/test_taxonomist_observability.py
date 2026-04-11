import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator

import pytest
from dotenv import load_dotenv
from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agents.clinical_taxonomist.agent import ClinicalTaxonomist
from app.shared.processing import safe_parse_json

load_dotenv()

class SpyTaxonomist(ClinicalTaxonomist):
    """
    A diagnostic wrapper for ClinicalTaxonomist that logs internal state
    transitions and agent handoffs to provide insight into mapping failures.
    """
    def __init__(self):
        super().__init__(name="spy_taxonomist")
        object.__setattr__(self, "logs", [])

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        self.logs.append("--- SpyTaxonomist Execution Start ---")
        
        # Log the input findings
        latest_event = ctx.session.events[-1]
        self.logs.append(f"Input Event Author: {latest_event.author}")
        for part in latest_event.content.parts:
            if part.text:
                self.logs.append(f"Input Part (truncated): {part.text[:200]}...")

        # Run internal logic and intercept results
        async for ev in super()._run_async_impl(ctx):
            if ev.is_final_response():
                data = safe_parse_json(ev)
                self.logs.append(f"Sub-Agent '{ev.author}' Final Response received.")
                if not data:
                    self.logs.append(f"!!! WARNING: Sub-Agent '{ev.author}' returned empty or invalid JSON.")
                    self.logs.append(f"RAW CONTENT: {ev.content.parts[0].text if ev.content and ev.content.parts else 'EMPTY'}")
                else:
                    self.logs.append(f"DATA: {json.dumps(data, indent=2, ensure_ascii=False)}")
                    # Check for empty results lists which often cause "Failed to map"
                    if "results" in data and not data["results"]:
                         self.logs.append(f"!!! CRITICAL: Sub-Agent '{ev.author}' returned an EMPTY results list.")
            yield ev
        
        self.logs.append("--- SpyTaxonomist Execution End ---")

@pytest.mark.asyncio
async def test_mapping_observability():
    """
    Integration test that uses SpyTaxonomist to surface WHY a document might
    fail mapping (e.g., empty sub-agent responses).
    """
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "global"

    spy = SpyTaxonomist()
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="obs_test", user_id="tester", session_id="s1")

    # Simulate a payload that might cause issues (e.g. very short or ambiguous findings)
    problematic_findings = [
        {
            "finding_id": "err_1",
            "nursing_diagnosis": "Pasienten er trøtt.", # Very generic
            "intervention": "Hvil",
            "goal": "Bedre form"
        }
    ]

    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id=str(uuid.uuid4()),
        agent=spy,
        run_config=RunConfig()
    )

    mapper_msg = types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(problematic_findings))])
    session.events.append(Event(author="system", content=mapper_msg))

    print("\nRunning Observability Test...")
    async for _ in spy.run_async(ctx):
        pass

    # Print the captured logs for insight
    print("\n--- INTERNAL OBSERVABILITY LOGS ---")
    for log in spy.logs:
        print(log)
    print("------------------------------------\n")

    assert any("Sub-Agent" in log for log in spy.logs), "No sub-agent responses were captured."
