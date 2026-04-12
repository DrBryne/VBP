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
    location = config.DEPLOYMENT_LOCATION # us-central1
    
    # Resource ID from our deployment
    with open("deployment_metadata.json") as f:
        meta = json.load(f)
        engine_id = meta["remote_agent_engine_id"]

    print(f"--- Starting Agent Engine Remote Full Run (US) ---")
    print(f"Target Agent: {engine_id}")

    # Use the higher-level client for better method mapping
    client = vertexai.Client(project=project_id, location=location)
    remote_agent = client.agent_engines.get(name=engine_id)

    # Configuration Payload - Load from central test_payload.json
    payload_path = "tests/test_payload.json"
    if not os.path.exists(payload_path):
        print(f"Test payload file missing: {payload_path}")
        return

    with open(payload_path) as f:
        payload = json.load(f)

    # In cloud, we always set some high-concurrency and full file count if not specified
    # but the JSON is the single source of truth for the target_group and gcs_uri.
    
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
            
            # The cloud Agent Engine sometimes strips the custom 'event_type' tag during SSE streaming.
            # We must manually sniff the content to see if it's the final JSON payload.
            content_str = ""
            if isinstance(ev_content, str):
                content_str = ev_content
            elif isinstance(ev_content, dict) and "parts" in ev_content:
                content_str = ev_content["parts"][0].get("text", "")
                
            if ev_type == "final_response" or '{"execution_summary":' in content_str:
                final_response_data = ev_content
                # Do not print the massive JSON to stdout
            elif content_str:
                print(f"[Cloud] {content_str}")

        end_time = datetime.now()
        duration = end_time - start_time
        print(f"\nWorkflow Completed in: {duration}")
        
        if final_response_data:
            print("\n--- Final Synthesis Received ---")
            
            # The final response is typically a JSON string in the first part
            json_str = ""
            if isinstance(final_response_data, str):
                json_str = final_response_data
            elif isinstance(final_response_data, dict) and "parts" in final_response_data:
                json_str = final_response_data["parts"][0].get("text", "")
                
            if json_str:
                try:
                    handover = json.loads(json_str)
                    summary = handover.get("summary", handover.get("execution_summary", {}))
                    synthesis_uri = handover.get("synthesis_uri")
                    
                    print("\n--- FINAL SYNTHESIS MANIFEST ---")
                    print(f"Status: {handover.get('status')}")
                    print(f"Run ID: {handover.get('run_id')}")
                    print(f"Synthesis GCS URI: {synthesis_uri}")
                    
                    if summary:
                        print(f"Processed Docs: {summary.get('processed_files_count')}")
                        print(f"Success Count: {summary.get('successful_files_count')}")
                        print(f"Finding Count: {summary.get('total_synthesized_findings')}")

                    # --- Sync local copy for local report generation ---
                    run_id = start_time.strftime("%Y-%m-%d_%H-%M-%S")
                    run_dir = f"tests/integration/results/run_{run_id}"
                    os.makedirs(run_dir, exist_ok=True)
                    
                    json_path = os.path.join(run_dir, "workflow_synthesis.json")
                    
                    if synthesis_uri:
                        print(f"Downloading full synthesis from GCS...")
                        from google.cloud import storage
                        bucket_name = synthesis_uri.split("/")[2]
                        blob_name = "/".join(synthesis_uri.split("/")[3:])
                        storage_client = storage.Client()
                        bucket = storage_client.bucket(bucket_name)
                        blob = bucket.blob(blob_name)
                        blob.download_to_filename(json_path)
                    else:
                        # Fallback for local testing or old versions
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(handover, f, indent=2, ensure_ascii=False)
                        
                    print(f"\nJSON Synthesis saved to: {json_path}")
                    print(f"Generating local HTML report...")
                    
                    local_report_path = os.path.join(run_dir, "report.html")
                    
                    # Run the report generator locally (using the new app/ path)
                    process = await asyncio.create_subprocess_exec(
                        "uv", "run", "python", "app/report_generator/main.py",
                        "--input", json_path,
                        "--output", local_report_path
                    )
                    await process.wait()
                    
                    if process.returncode == 0:
                        print(f"Local report generated: {local_report_path}")
                        report_url = handover.get("report_url", f"https://storage.cloud.google.com/{config.BASE_BUCKET.replace('gs://', '')}/reports/latest_vbp_report.html")
                        print(f"\n[VIEW LATEST CLOUD REPORT]: {report_url}")
                        
                except Exception as parse_e:
                    print(f"Error parsing final response JSON: {parse_e}")
                    print("Received raw final response (unable to parse summary).")
            else:
                print("Final response was empty or malformed.")
            
    except Exception as e:
        print(f"\n[ERROR] Remote execution failed: {e}")

if __name__ == "__main__":
    asyncio.run(run_remote_cloud_test())
