import os
from dataclasses import dataclass


@dataclass(frozen=True)
class VBPConfig:
    """
    Central configuration registry for the VBP Workflow.
    Reads from environment variables with sensible defaults for the 
    European staging environment.
    """
    # Google Cloud Project Details
    PROJECT_ID: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "sunny-passage-362617")

    # The location where the Agent Engine managed service lives (API Control Plane)
    DEPLOYMENT_LOCATION: str = os.environ.get("VBP_DEPLOY_LOCATION", "us-central1")

    # The location where the clinical data processing occurs (Staging region)
    PROCESSING_LOCATION: str = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    # Storage Configuration
    # Note: Clinical data is now standardized in the US multi-region
    BASE_BUCKET: str = os.environ.get("VBP_DATA_BUCKET", "gs://veiledende_behandlingsplan")
    
    # Path to the ALS clinical documents
    ALS_DOCS_URI: str = f"{BASE_BUCKET}/ALS/"

    # Path for the latest automated clinical report
    GLOBAL_REPORT_URI: str = f"{BASE_BUCKET}/reports/latest_vbp_report.html"

    # Path for the persistent terminology cache
    TAXONOMY_CACHE_URI: str = f"{BASE_BUCKET}/cache/taxonomy_cache.json"

    # Taxonomy Configuration
    # We use 'global' for preview models until they are regionalized in Europe
    PREVIEW_MODEL_LOCATION: str = "global"

# Singleton instance for easy import
config = VBPConfig()
