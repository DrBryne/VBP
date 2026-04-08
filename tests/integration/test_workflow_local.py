import os
import asyncio
import json
import warnings
from dotenv import load_dotenv

# Load environment variables before ADK imports
load_dotenv()

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from app.agent import root_agent

async def run_local_test():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    
    # Force Vertex AI backend and use global location for preview models
    location = "global"
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    os.environ["GOOGLE_CLOUD_LOCATION"] = location
    
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT not set. Please set it in .env or your environment.")
        return

    # Define test parameters extracted from the original script
    test_gcs_uri = "gs://veiledende_behandlingsplan/ALS/" 
    test_target_group = "ALS - Amytrofisk lateral sklerose"
    limit_files = None # No limit on number of files
    max_concurrency = 10 # Sustainable concurrency for high quota reliability
    
    print(f"--- Starting ADK 2.0 Local Workflow Test ---")
    print(f"Project: {project_id}")
    print(f"Model Location: {location}")
    print(f"Target Group: {test_target_group}")
    print(f"Source Bucket: {test_gcs_uri}")
    print(f"File Limit: {limit_files}")
    print(f"Max Concurrency: {max_concurrency}")
    print(f"--------------------------------------------\n")

    # Initialize the ADK Runner
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name="vbp_workflow_test",
        session_service=session_service
    )
    
    # We explicitly create the session
    session_id = "test-session-1"
    user_id = "local-tester"
    session = await session_service.create_session(app_name="vbp_workflow_test", user_id=user_id, session_id=session_id)
    
    # We send the configuration as a JSON message to start the workflow
    # This avoids the ADK 2.0 InMemorySessionService state mutation issue
    config_payload = json.dumps({
        "gcs_uri": test_gcs_uri,
        "target_group": test_target_group,
        "max_files": limit_files,
        "max_concurrency": max_concurrency
    })
    start_msg = types.Content(role="user", parts=[types.Part.from_text(text=config_payload)])

    print("Executing workflow...")
    
    final_result_text = None
    
    # Iterate through the events yielded by the workflow agent
    async for event in runner.run_async(
        user_id=user_id, 
        session_id=session_id,
        new_message=start_msg
    ):
        # Print intermediate log messages from the orchestrator
        if event.content and event.content.parts:
            text = event.content.parts[0].text
            print(f"[Agent]: {text}")
            
            # The final response from the Consolidator will be the synthesis
            if event.is_final_response():
                final_result_text = text

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
            
            print("\n--- Workflow Synthesis Result ---")
            print(formatted_json)
            
            os.makedirs("tests/integration/results", exist_ok=True)
            output_file = "tests/integration/results/workflow_synthesis.json"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(formatted_json)
            print(f"\nResult saved to: {output_file}")
            
        except json.JSONDecodeError:
            print("\n--- Workflow Result (Raw Text) ---")
            print(final_result_text)
    else:
        print("\nWorkflow completed, but no final synthesis result was returned.")
        
    await runner.close()
    await asyncio.sleep(0.25)

if __name__ == "__main__":
    asyncio.run(run_local_test())
