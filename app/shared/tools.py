import json
import os

from google.cloud import storage

from app.app_utils.telemetry import track_telemetry_span

...

@track_telemetry_span("GCS: Download JSON")
def download_json_from_gcs(gcs_uri: str, project_id: str) -> dict | None:
    """Downloads and parses a JSON file from GCS."""
    try:
        bucket_name, blob_name = parse_gcs_uri(gcs_uri)
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            return None

        content = blob.download_as_text()
        return json.loads(content)
    except Exception as e:
        from app.shared.logging import VBPLogger
        VBPLogger("tools").error(f"Failed to download JSON from {gcs_uri}: {e}")
        return None

@track_telemetry_span("GCS: Upload JSON")
def upload_json_to_gcs(data: dict, gcs_uri: str, project_id: str):
    """Serializes and uploads a dictionary to GCS as JSON."""
    try:
        bucket_name, blob_name = parse_gcs_uri(gcs_uri)
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        blob.upload_from_string(
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json"
        )
    except Exception as e:
        from app.shared.logging import VBPLogger
        VBPLogger("tools").error(f"Failed to upload JSON to {gcs_uri}: {e}")


def parse_gcs_uri(gcs_uri: str) -> tuple[str, str]:
    """Parses a gs:// URI into a (bucket_name, prefix/blob_name) tuple."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError("GCS URI must start with gs://")

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket_name, prefix

@track_telemetry_span("GCS: List Files")
def list_gcs_files(gcs_uri: str, project_id: str) -> list[str]:
    """Lists all files in a GCS bucket/prefix."""
    bucket_name, prefix = parse_gcs_uri(gcs_uri)

    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=prefix)

    return [f"gs://{bucket_name}/{blob.name}" for blob in blobs if not blob.name.endswith("/")]

def load_prompt(prompt_name: str) -> str:
    """
    Centralized utility to load prompt text files from the app/prompts directory.
    Uses absolute pathing relative to the app root for portability.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.dirname(current_dir)
    prompt_path = os.path.join(app_root, "prompts", prompt_name)

    if not prompt_name.endswith(".txt"):
        prompt_path += ".txt"

    try:
        with open(prompt_path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError as err:
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}") from err
