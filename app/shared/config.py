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

    # Path to the clinical documents (configurable via environment)
    ALS_DOCS_URI: str = os.environ.get("VBP_GCS_URI", f"{BASE_BUCKET}/ALS/")

    # Target Group name (configurable via environment)
    TARGET_GROUP: str = os.environ.get("VBP_TARGET_GROUP", "ALS - Amytrofisk lateral sklerose")

    # Path for the latest automated clinical report
    GLOBAL_REPORT_URI: str = f"{BASE_BUCKET}/reports/draft_vbp_report.html"

    # Path for the persistent terminology cache
    TAXONOMY_CACHE_URI: str = f"{BASE_BUCKET}/cache/taxonomy_cache.json"

    # Taxonomy Configuration
    # We use 'global' for preview models until they are regionalized in Europe
    PREVIEW_MODEL_LOCATION: str = "global"

    # Synthesis Distillation Thresholds
    # Minimum number of documents that must agree to admit a finding without Level 1/2 evidence
    CONSENSUS_THRESHOLD: int = int(os.environ.get("VBP_CONSENSUS_THRESHOLD", "3"))

    # Threshold for findings in a Functional Area (FO) to be considered 'cluttered'
    CLUTTER_THRESHOLD: int = int(os.environ.get("VBP_CLUTTER_THRESHOLD", "5"))

    # Required percentage (0.0 - 1.0) of findings in a cluttered FO that must share a parent to merge
    PARENT_COVERAGE_PERCENT: float = float(os.environ.get("VBP_PARENT_COVERAGE_PERCENT", "0.5"))

    # Minimum SNOMED hierarchy depth required to allow merging under a non-refset parent
    # Depth 0=Root, 1=Clinical finding, 2=Functional finding, 3=Category, 4=System finding, 5=System sub-finding
    MIN_MERGE_DEPTH: int = int(os.environ.get("VBP_MIN_MERGE_DEPTH", "5"))

# Singleton instance for easy import
config = VBPConfig()
