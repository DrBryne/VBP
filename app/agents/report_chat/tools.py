import json
import logging
from typing import Optional

from google.adk.tools import ToolContext
from google.cloud import storage

logger = logging.getLogger(__name__)

async def read_synthesis_report(gcs_path: str, query: Optional[str] = None, tool_context: Optional[ToolContext] = None) -> dict:
    """Reads a clinical synthesis JSON manifest from Google Cloud Storage (GCS) to answer user questions about the synthesis results.
    
    Args:
        gcs_path: The full GCS URI to the workflow_synthesis.json file (e.g. gs://bucket/path/workflow_synthesis.json).
        query: Optional specific term, diagnosis, or keyword to filter the JSON results by before returning.
        
    Returns:
        dict: A dictionary containing the status and the extracted data from the report.
    """
    if not gcs_path.startswith("gs://"):
        return {"status": "error", "message": "Invalid GCS path. Must start with gs://"}

    try:
        bucket_name = gcs_path.replace("gs://", "").split("/")[0]
        blob_name = "/".join(gcs_path.replace("gs://", "").split("/")[1:])
        
        storage_client = storage.Client()
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        if not blob.exists():
            return {"status": "error", "message": f"File not found at {gcs_path}"}
            
        json_data = blob.download_as_string()
        report_data = json.loads(json_data)
        
        # Perform basic filtering if query is provided to prevent LLM context overflow
        # VBP json has 'final_groups' array which contains the synthesized clusters
        if query and "final_groups" in report_data:
            filtered_groups = []
            query_lower = query.lower()
            for group in report_data["final_groups"]:
                group_str = json.dumps(group).lower()
                if query_lower in group_str:
                    filtered_groups.append(group)
                    
            if filtered_groups:
                report_data["final_groups"] = filtered_groups
            else:
                return {"status": "success", "message": f"No groups matched query '{query}'. Try a broader term."}

        # Truncate if massive, though VBP groups are usually well-structured
        return {"status": "success", "data": report_data}
        
    except Exception as e:
        logger.error(f"Failed to read synthesis report from {gcs_path}: {e}")
        return {"status": "error", "message": str(e)}
