import asyncio
import json
import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from app.agent import root_agent
from app.agents.clinical_taxonomist.agent import ClinicalTaxonomist
from app.shared.logging import VBPLogger
from app.shared.models import WorkflowProgress
from app.shared.processing import process_document_pipeline, safe_parse_json

load_dotenv()

# Logger for the diagnostic test
logger = VBPLogger("taxonomist_deep_dive")

# THE FAILING DOCUMENT
FAILING_URI = "gs://veiledende_behandlingsplan/ALS/35309870_fulltext.xml"

class SpyTaxonomist(ClinicalTaxonomist):
    """
    A diagnostic wrapper for ClinicalTaxonomist that logs internal state
    transitions and agent handoffs to provide insight into mapping failures.
    """
    def __init__(self):
        super().__init__(name="spy_taxonomist")
        object.__setattr__(self, "logs", [])

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event]:
        self.logs.append("--- SpyTaxonomist Execution Start ---")

        # Log the input findings
        latest_event = ctx.session.events[-1]
        self.logs.append(f"Input Event Author: {latest_event.author}")
        for part in latest_event.content.parts:
            if part.text:
                self.logs.append(f"Input Part (truncated): {part.text[:300]}...")

        # Run internal logic and intercept results
        async for ev in super()._run_async_impl(ctx):
            if ev.is_final_response():
                data = safe_parse_json(ev)
                self.logs.append(f"\n[FINAL RESPONSE] Sub-Agent '{ev.author}'")
                if not data:
                    self.logs.append(f"!!! WARNING: Sub-Agent '{ev.author}' returned empty or invalid JSON.")
                    self.logs.append(f"RAW CONTENT: {ev.content.parts[0].text if ev.content and ev.content.parts else 'EMPTY'}")
                else:
                    self.logs.append(f"DATA: {json.dumps(data, indent=2, ensure_ascii=False)}")
                    if "results" in data and not data["results"]:
                         self.logs.append(f"!!! CRITICAL: Sub-Agent '{ev.author}' returned an EMPTY results list.")
            yield ev

        self.logs.append("\n--- SpyTaxonomist Execution End ---")

async def run_deep_dive():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = location

    print(f"\n--- Starting Deep Dive: {FAILING_URI} ---")

    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="diagnostic", session_service=session_service)

    # Main Session
    session = await session_service.create_session(app_name="diagnostic", user_id="debug", session_id="deep_dive_run")

    spy_taxonomist = SpyTaxonomist()
    progress_queue = asyncio.Queue()
    progress_state = WorkflowProgress()
    state_lock = asyncio.Lock()
    ephemeral_session_service = InMemorySessionService()

    # Mock context
    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id="diag_dive_1",
        agent=root_agent,
        run_config=RunConfig()
    )

    FAILING_URI.split("/")[-1]

    try:
        # We replace the taxonomist with our spy for THIS call
        result = await process_document_pipeline(
            uri=FAILING_URI,
            target_group="ALS - Amytrofisk lateral sklerose",
            project_id=project_id,
            clinical_extractor=root_agent.extractor,
            clinical_taxonomist=spy_taxonomist, # SPY INJECTED HERE
            clinical_auditor=root_agent.auditor,
            parent_ctx=ctx,
            ephemeral_session_service=ephemeral_session_service,
            progress_state=progress_state,
            state_lock=state_lock,
            progress_queue=progress_queue
        )

        if hasattr(result, "justification"):
            print(f"\nRESULT: Document EXCLUDED - {result.justification}")
        else:
            print(f"\nRESULT: Document SUCCESS - {len(result.mapped_findings)} findings mapped.")
    except Exception as e:
        print(f"\nRESULT: PIPELINE CRASHED - {e}")

    # Output the spy logs
    print("\n" + "="*60)
    print("DETAILED MAPPING INSIGHTS")
    print("="*60)
    for log in spy_taxonomist.logs:
        print(log)
    print("="*60 + "\n")

    await runner.close()

if __name__ == "__main__":
    asyncio.run(run_deep_dive())
