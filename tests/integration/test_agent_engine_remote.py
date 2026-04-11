import asyncio
import json
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import vertexai
from vertexai.preview import reasoning_engines
from app.shared.config import config

async def run_remote_cloud_test():
    """
    Triggers a full 96-document synthesis run on the deployed Agent Engine.
    Uses the canonical ADK 2.0 stream_query pattern.
    """
    project_id = config.PROJECT_ID
    location = config.DEPLOYMENT_LOCATION # europe-west1
    
    # Resource ID from our Belgian deployment
    engine_id = "projects/293859476528/locations/europe-west1/reasoningEngines/8567649690328236032"

    print(f"--- Starting Agent Engine Remote Full Run (EUROPE) ---")
    print(f"Target Agent: {engine_id}")

    # Use the higher-level client for better method mapping
    client = vertexai.Client(project=project_id, location=location)
    remote_agent = client.agent_engines.get(name=engine_id)

    # Configuration Payload
    payload = {
        "project_id": project_id,
        "location": config.PROCESSING_LOCATION, # Belgium
        "target_group": "ALS - Amytrofisk lateral sklerose",
        "bucket_uri": config.ALS_DOCS_URI,
        "max_concurrency": 30,
        "limit": 96
    }
    
    # ADK 2.0 requires an invocation_id for cloud events
    run_config = {
        "custom_config": payload,
        "invocation_id": str(uuid.uuid4())
    }

    start_time = datetime.now()
    print(f"Workflow Triggered at: {start_time.isoformat()}")

    try:
        print("Streaming results from cloud...")
        # Direct call to stream_query as defined in AdkApp
        # We pass the full JSON payload as the 'message' since the orchestrator 
        # is already designed to fallback to parsing the latest message text.
        response_stream = remote_agent.stream_query(
            message=json.dumps(payload),
            user_id="test_user_remote"
        )
        
        final_response_data = None
        for event in response_stream:
            # Handle different event formats (dict or object)
            ev_type = event.get("event_type") if isinstance(event, dict) else getattr(event, "event_type", None)
            ev_content = event.get("content") if isinstance(event, dict) else getattr(event, "content", None)
            
            if ev_type == "final_response":
                final_response_data = ev_content
            elif ev_content:
                # Print progress updates
                if isinstance(ev_content, str):
                    print(f"[Cloud] {ev_content}")
                elif isinstance(ev_content, dict) and "parts" in ev_content:
                    print(f"[Cloud] {ev_content['parts'][0].get('text')}")

        end_time = datetime.now()
        duration = end_time - start_time
        print(f"\nWorkflow Completed in: {duration}")
        
        if final_response_data:
            print("\n--- Final Synthesis Received ---")
            # Automatically generated report link
            print(f"\n[VIEW LATEST REPORT]: https://storage.googleapis.com/{config.GLOBAL_REPORT_URI[5:]}")
            
    except Exception as e:
        print(f"\n[ERROR] Remote execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_remote_cloud_test())
