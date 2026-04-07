import os
from typing import List
from google.cloud import storage

def list_gcs_files(gcs_uri: str, project_id: str) -> List[str]:
    """Lists all files in a GCS bucket/prefix."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError("GCS URI must start with gs://")
    
    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    
    storage_client = storage.Client(project=project_id)
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=prefix)
    
    return [f"gs://{bucket_name}/{blob.name}" for blob in blobs if not blob.name.endswith("/")]
