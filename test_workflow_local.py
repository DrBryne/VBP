import os
import asyncio
import json
from dotenv import load_dotenv
from agents.vbp_workflow import VBPWorkflow
from vertexai.agent_engines import AdkApp

# Load environment variables (ensure GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION are set)
load_dotenv()

async def test_workflow():
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    # For Gemini 3.1 Pro Preview, the user specified "global"
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT not set in .env")
        return

    # 1. Initialize the Workflow
    # Note: GCS operations will use the project credentials regardless of LLM location
    vbp_workflow = VBPWorkflow(project_id=project_id, location=location)
    
    # 2. Define test parameters
    # Update these to match your actual test data in GCS
    test_gcs_uri = "gs://veiledende_behandlingsplan/ALS/" 
    test_target_group = "ALS - Amytrofisk lateral sklerose"
    limit_files = None # No limit on files
    max_concurrency = 100 # Safe concurrency limit (10) for preview models to avoid persistent 429s
    
    print(f"--- Starting Local Workflow Test ---")
    print(f"Project: {project_id}")
    print(f"Model Location: {location}")
    print(f"Target Group: {test_target_group}")
    print(f"Source Bucket: {test_gcs_uri}")
    print(f"File Limit: {limit_files}")
    print(f"Max Concurrency: {max_concurrency}")
    print(f"-------------------------------------\n")

    # 3. Run the workflow directly for local debugging
    try:
        result = await vbp_workflow.run(
            gcs_uri=test_gcs_uri, 
            target_group=test_target_group, 
            max_files=limit_files,
            max_concurrency=max_concurrency
        )
        
        if result:
            print("\n--- Workflow Synthesis Result ---")
            print(result.model_dump_json(indent=2))
            
            # Save to file for inspection
            output_file = "workflow_test_result.json"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=2))
            print(f"\nResult saved to: {output_file}")
        else:
            print("\nWorkflow failed to return a result.")
            
    except Exception as e:
        print(f"\nAn error occurred during the workflow run: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_workflow())
