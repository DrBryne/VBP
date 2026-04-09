import os
import asyncio
import json
import warnings
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables before ADK imports
load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent
from app.shared.logging import VBPLogger

# Initialize test logger
logger = VBPLogger("test_workflow")

async def run_local_test():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = f"tests/integration/results/run_{run_id}"
    os.makedirs(run_dir, exist_ok=True)
    log_file_path = os.path.join(run_dir, "session.log")
    
    # Force Vertex AI backend and use global location for preview models
    location = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = location
    
    if not project_id:
        logger.error("GOOGLE_CLOUD_PROJECT not set. Please set it in .env or your environment.")
        return

    # Define test parameters
    test_gcs_uri = "gs://veiledende_behandlingsplan/ALS/" 
    test_target_group = "ALS - Amytrofisk lateral sklerose"
    limit_files = 3 # Process 3 files
    max_concurrency = 3 # Lower concurrency for limited test
    
    logger.info(f"--- Starting ADK 2.0 Local Workflow Test (Run ID: {run_id}) ---", 
                project=project_id, 
                location=location,
                target_group=test_target_group,
                bucket=test_gcs_uri,
                results_dir=run_dir)
    
    # Initialize the ADK Runner with a session service
    session_service = InMemorySessionService()
    runner = Runner(
        session_service=session_service,
        app_name="vbp_workflow",
        agent=root_agent
    )
    session_id = f"test-session-{run_id}"
    user_id = "test-user"

    # Create the session
    await session_service.create_session(
        app_name="vbp_workflow", 
        user_id=user_id, 
        session_id=session_id
    )

    # Define the initial configuration message for the root agent
    start_msg = types.Content(
        role="user",
        parts=[types.Part.from_text(text=json.dumps({
            "gcs_uri": test_gcs_uri,
            "target_group": test_target_group,
            "max_files": limit_files,
            "max_concurrency": max_concurrency
        }))]
    )

    final_result_text = None
    logger.info("Executing workflow...")
    
    with open(log_file_path, "w", encoding="utf-8") as log_file:
        log_file.write(f"Run ID: {run_id}\n")
        log_file.write(f"Timestamp: {datetime.now().isoformat()}\n")
        log_file.write("-" * 40 + "\n")
        
        # Iterate through the events yielded by the workflow agent
        async for event in runner.run_async(
            user_id=user_id, 
            session_id=session_id,
            new_message=start_msg
        ):
            # Print intermediate log messages from the orchestrator
            if event.content and event.content.parts:
                text = event.content.parts[0].text
                
                # The final response from the Consolidator will be the synthesis.
                # We capture it here but don't print it yet to avoid double-printing.
                if event.is_final_response():
                    final_result_text = text
                else:
                    msg = f"[Agent Event] {text}"
                    logger.info(msg)
                    log_file.write(f"{datetime.now().isoformat()} - {msg}\n")
                    log_file.flush()

    if final_result_text:
        # Try to parse and format the JSON beautifully
        try:
            # Clean up markdown code block formatting if present
            clean_text = final_result_text
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
                
            parsed_json = json.loads(clean_text.strip())
            formatted_json = json.dumps(parsed_json, indent=2, ensure_ascii=False)
            
            output_file = os.path.join(run_dir, "workflow_synthesis.json")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(formatted_json)
            logger.info("--- Final Synthesis Complete ---", output_file=output_file)
            
        except json.JSONDecodeError:
            logger.warning("Failed to parse synthesis as JSON. Saving raw text.")
            output_file = os.path.join(run_dir, "workflow_synthesis_raw.txt")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_result_text)
            logger.info("Raw result saved", output_file=output_file)
    else:
        logger.warning("Workflow completed, but no final synthesis result was returned.")
        
    await runner.close()
    await asyncio.sleep(0.25)

if __name__ == "__main__":
    asyncio.run(run_local_test())
