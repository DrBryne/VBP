import asyncio
import os

# Load environment variables
from dotenv import load_dotenv
from google.adk.agents.invocation_context import InvocationContext, RunConfig
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.shared.logging import VBPLogger
from app.shared.models import WorkflowProgress
from app.shared.processing import process_document_pipeline

load_dotenv()

# Logger for the diagnostic test
logger = VBPLogger("taxonomist_debug")

# The 10 known failing documents from the last run
FAILING_URIS = [
    "gs://veiledende_behandlingsplan/ALS/250-254.pdf",
    "gs://veiledende_behandlingsplan/ALS/35309870_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/36414305_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/36834005_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/36995270_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/37254833_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/37610446_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/38345764_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/38622643_fulltext.xml",
    "gs://veiledende_behandlingsplan/ALS/38762656_fulltext.xml"
]

async def run_diagnostic():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = location

    print("--- Starting Focused Taxonomist Diagnostic Test ---")
    print(f"Processing {len(FAILING_URIS)} documents that previously failed mapping.")

    # We use a runner to invoke the root agent with specific file paths
    session_service = InMemorySessionService()
    runner = Runner(agent=root_agent, app_name="diagnostic", session_service=session_service)

    # Send custom configuration event to the main session
    session = await session_service.create_session(app_name="diagnostic", user_id="debug", session_id="debug_run")
    init_msg = types.Content(role="user", parts=[types.Part.from_text(text=f"Process these specific files: {','.join(FAILING_URIS)}")])
    session.events.append(Event(author="user", content=init_msg))

    progress_queue = asyncio.Queue()
    progress_state = WorkflowProgress()
    state_lock = asyncio.Lock()
    ephemeral_session_service = InMemorySessionService()

    # Mock context
    ctx = InvocationContext(
        session=session,
        session_service=session_service,
        invocation_id="diag_1",
        agent=root_agent,
        run_config=RunConfig()
    )

    for uri in FAILING_URIS:
        filename = uri.split("/")[-1]
        print(f"\n>>> DEBUGGING: {filename}")

        try:
            result = await process_document_pipeline(
                uri=uri,
                target_group="ALS - Amytrofisk lateral sklerose",
                project_id=project_id,
                clinical_extractor=root_agent.extractor,
                clinical_taxonomist=root_agent.taxonomist,
                clinical_auditor=root_agent.auditor,
                parent_ctx=ctx,
                ephemeral_session_service=ephemeral_session_service,
                progress_state=progress_state,
                state_lock=state_lock,
                progress_queue=progress_queue
            )

            if hasattr(result, "justification"):
                print(f"RESULT: Document EXCLUDED - {result.justification}")
            else:
                print(f"RESULT: Document SUCCESS - {len(result.mapped_findings)} findings mapped.")
        except Exception as e:
            print(f"RESULT: PIPELINE CRASHED for {filename} - {e}")

    await runner.close()
    print("\n--- Diagnostic Test Complete ---")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())


if __name__ == "__main__":
    asyncio.run(run_diagnostic())
