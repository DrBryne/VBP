import os
import json
from agent import create_agent

def manual_test_with_real_gcs():
    """
    A template for running a real test. Requires:
    1. Authenticated gcloud environment
    2. A valid GCS URI with a clinical document
    """
    target_group = "ALS - Amytrofisk lateral sklerose"
    gcs_uri = "gs://veiledende_behandlingsplan/ALS/40802439_fulltext.xml"

    print(f"\nAttempting real test with URI: {gcs_uri}")
    try:
        # Check if we have credentials before attempting
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            print("Skipping real test: GOOGLE_CLOUD_PROJECT not set.")
            return

        response = create_agent(target_group, gcs_uri)
        
        # Write the JSON output to a file in the project root
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        output_file_path = os.path.join(root_dir, "analysis_result.json")
        
        with open(output_file_path, "w", encoding="utf-8") as f:
            if response:
                json.dump(response.model_dump(), f, indent=2, ensure_ascii=False)
            
        print(f"Successfully wrote the API Response to {output_file_path}")
        
    except Exception as e:
        print(f"Skipping real test (execution failed): {e}")

if __name__ == "__main__":
    manual_test_with_real_gcs()