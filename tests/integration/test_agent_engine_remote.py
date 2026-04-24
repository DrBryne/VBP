import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from threading import Thread

from dotenv import load_dotenv

load_dotenv()

import vertexai
from google.cloud import logging as gcp_logging

from app.shared.config import config


def trigger_agent_and_disconnect(remote_agent, payload):
    """
    Triggers the Agent Engine workflow and immediately disconnects.
    The server-side asyncio tasks will continue processing in the background
    unaffected by the client dropping the SSE connection.
    """
    try:
        print("[Trigger] Submitting job to Agent Engine...")
        response_stream = remote_agent.stream_query(
            message=json.dumps(payload),
            user_id="test_user_remote"
        )
        # Read the first event to guarantee the server has started execution
        next(response_stream)
        print("[Trigger] Job started successfully. Closing connection to run detached...")
        response_stream.close()
    except Exception:
        # A generator exit or disconnect exception is expected here
        pass

async def run_remote_cloud_test(args=None):
    """
    Submits a full synthesis run and monitors progress via Cloud Logging
    to avoid HTTP connection timeouts on massive document sets.
    """
    project_id = config.PROJECT_ID
    location = config.DEPLOYMENT_LOCATION # us-central1

    with open("deployment_metadata.json") as f:
        meta = json.load(f)
        engine_id = meta["remote_agent_engine_id"]

    reasoning_engine_id_short = engine_id.split("/")[-1]

    print("--- Starting Detached Agent Engine Run ---")
    print(f"Target Agent: {engine_id}")

    client = vertexai.Client(project=project_id, location=location)
    remote_agent = client.agent_engines.get(name=engine_id)

    payload_path = "tests/test_payload.json"
    if not os.path.exists(payload_path):
        print(f"Test payload file missing: {payload_path}")
        return

    with open(payload_path) as f:
        payload = json.load(f)

    if args and getattr(args, "quick", False):
        payload["max_files"] = 2
        print("\n[Config] QUICK MODE enabled: Processing only 2 files.")
    elif args and getattr(args, "max_files", None) is not None:
        payload["max_files"] = args.max_files
        print(f"\n[Config] MAX FILES overridden: {args.max_files}")

    # Capture start time in UTC for logging query
    start_time_utc = datetime.now(UTC)
    start_time_str = start_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Start the trigger in a background thread so it can be killed/closed
    # without blocking our log tailing loop.
    trigger_thread = Thread(target=trigger_agent_and_disconnect, args=(remote_agent, payload))
    trigger_thread.start()

    print("\n[Monitor] Tailing Cloud Logging for progress...\n")

    logging_client = gcp_logging.Client(project=project_id)

    filter_str = (
        f'('
        f'(resource.type="aiplatform.googleapis.com/ReasoningEngine" AND resource.labels.reasoning_engine_id="{reasoning_engine_id_short}") '
        f'OR logName="projects/{project_id}/logs/otel_python_inprocess_log_name_temp"'
        f') '
        f'AND timestamp >= "{start_time_str}" '
        f'AND (severity>="INFO" OR textPayload:"Progress" OR jsonPayload.name:"agent_call")'
    )

    seen_insert_ids = set()
    job_completed = False
    synthesis_uri = None

    while not job_completed:
        try:
            # Poll logs, descending order to get the newest, max 100 to avoid huge payloads
            entries = list(logging_client.list_entries(
                filter_=filter_str,
                order_by=gcp_logging.DESCENDING,
                max_results=100
            ))

            # Reverse to print oldest first
            entries.reverse()

            for entry in entries:
                if entry.insert_id in seen_insert_ids:
                    continue
                seen_insert_ids.add(entry.insert_id)

                # Handle telemetry JSON payloads from ADK
                if isinstance(entry.payload, dict) and entry.payload.get("name") == "agent_call":
                    agent_name = entry.payload.get("attributes", {}).get("agent.name", "unknown_agent")
                    print(f"[Cloud] 🤖 Agent Triggered: {agent_name}")
                    continue

                # Some payloads are JSON, some are text
                payload_text = entry.payload if isinstance(entry.payload, str) else json.dumps(entry.payload)

                if payload_text:
                    clean_text = payload_text.strip()
                    # Filter out HTTP noise and verbose system logs
                    if "HTTP/1.1" not in clean_text and "Cannot write log that is" not in clean_text and "aiplatform.googleapis.com" not in clean_text:

                        # Strip the annoying Vertex AI prefix if present: "[12]      INFO:     "
                        if "]      INFO:     " in clean_text:
                            print(f"[Cloud] {clean_text.split(']      INFO:     ')[-1].strip()}")
                        elif "]      ERROR:    " in clean_text:
                            print(f"[Cloud] ERROR: {clean_text.split(']      ERROR:    ')[-1].strip()}")
                        elif "]      WARNING:  " in clean_text:
                            pass # skip standard warnings
                        else:
                            print(f"[Cloud] {clean_text}")

                if payload_text and "Successfully backed up final synthesis to" in payload_text:
                    synthesis_uri = payload_text.split("Successfully backed up final synthesis to ")[1].strip()

                if payload_text and "Consolidation complete" in payload_text:
                    job_completed = True
        except Exception as e:
            print(f"[Monitor] Error fetching logs: {e}")

        if not job_completed:
            time.sleep(10)

    print(f"\nWorkflow Completed in: {datetime.now(UTC) - start_time_utc}")

    if synthesis_uri:
        print("\n--- FINAL SYNTHESIS MANIFEST ---")
        print(f"Synthesis GCS URI: {synthesis_uri}")

        # --- Sync local copy for local report generation ---
        run_id = start_time_utc.strftime("%Y-%m-%d_%H-%M-%S")
        run_dir = f"tests/integration/results/run_{run_id}"
        os.makedirs(run_dir, exist_ok=True)

        json_path = os.path.join(run_dir, "workflow_synthesis.json")

        print("Downloading full synthesis from GCS...")
        try:
            from google.cloud import storage
            bucket_name = synthesis_uri.split("/")[2]
            blob_name = "/".join(synthesis_uri.split("/")[3:])
            storage_client = storage.Client(project=project_id)
            bucket = storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            blob.download_to_filename(json_path)

            print(f"\nJSON Synthesis saved to: {json_path}")
            print("Generating local HTML report...")

            local_report_path = os.path.join(run_dir, "report.html")

            process = await asyncio.create_subprocess_exec(
                "uv", "run", "python", "app/report_generator/main.py",
                "--input", json_path,
                "--output", local_report_path
            )
            await process.wait()

            if process.returncode == 0:
                print(f"Local report generated: {local_report_path}")
                report_url = f"https://storage.cloud.google.com/{config.BASE_BUCKET.replace('gs://', '')}/reports/latest_vbp_report.html"
                print(f"\n[VIEW LATEST CLOUD REPORT]: {report_url}")
        except Exception as e:
            print(f"Failed to download or generate report: {e}")
    else:
        print("Failed to extract synthesis URI from logs.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trigger Agent Engine test run.")
    parser.add_argument("--quick", action="store_true", help="Run a quick test (max 2 files).")
    parser.add_argument("--max-files", type=int, help="Override max files to process.")
    args = parser.parse_args()
    asyncio.run(run_remote_cloud_test(args))
