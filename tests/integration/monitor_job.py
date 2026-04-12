import subprocess
import time
import json
import os
from datetime import datetime, timedelta

PROJECT_ID = "sunny-passage-362617"
REASONING_ENGINE_ID = "1977863638450438144"
LOG_DIR = "tests/integration/results"
LOG_FILE = os.path.join(LOG_DIR, "integration_test_monitor.log")

# Catch logs from the last 15 minutes to re-populate the file with recent activity
last_timestamp = (datetime.utcnow() - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")

def log_to_file(message):
    with open(LOG_FILE, "a") as f:
        f.write(f"{message}\n")
        f.flush()

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_to_file(f"\n--- Monitoring Re-Started at {datetime.now()} (UTC) ---")
log_to_file(f"Target Project: {PROJECT_ID}")
log_to_file(f"Target Reasoning Engine: {REASONING_ENGINE_ID}\n")

while True:
    # Filter for all logs (INFO, WARNING, ERROR)
    filter_str = (
        f'resource.type="aiplatform.googleapis.com/ReasoningEngine" '
        f'AND resource.labels.reasoning_engine_id="{REASONING_ENGINE_ID}" '
        f'AND timestamp > "{last_timestamp}"'
    )
    
    cmd = [
        "gcloud", "logging", "read", filter_str,
        f"--project={PROJECT_ID}",
        "--format=json",
        "--order=asc"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            entries = json.loads(result.stdout)
            if entries:
                for entry in entries:
                    ts = entry.get("timestamp")
                    payload = entry.get("textPayload", entry.get("jsonPayload", "No payload"))
                    severity = entry.get("severity", "INFO")
                    log_to_file(f"[{ts}] [{severity}] {payload}")
                    last_timestamp = ts
        else:
            log_to_file(f"[{datetime.now()}] gcloud error: {result.stderr}")
            
    except Exception as e:
        log_to_file(f"[{datetime.now()}] Monitor script exception: {str(e)}")
        
    time.sleep(30)
