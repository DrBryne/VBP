import os
from typing import List, Tuple
from google.cloud import storage

def parse_gcs_uri(gcs_uri: str) -> Tuple[str, str]:
    """Parses a gs:// URI into a (bucket_name, prefix/blob_name) tuple."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError("GCS URI must start with gs://")
    
    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return bucket_name, prefix

def list_gcs_files(gcs_uri: str, project_id: str) -> List[str]:
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
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
