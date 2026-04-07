import os
import sys
import json
from pydantic import ValidationError

# Set up paths
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "agents"))

from shared.models import ResponseSchema, SourceDocumentEnriched, FindingEnriched
from term_mapper.agent import create_term_mapper_agent

def test_mapping_with_real_data():
    # 1. Load real data from analysis_result.json in the project root
    input_file = os.path.join(project_root, "analysis_result.json")
    
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    try:
        with open(input_file, "r", encoding="utf-8") as f:
            raw_json = f.read()
        
        # Parse into ResponseSchema
        input_data = ResponseSchema.model_validate_json(raw_json)
        
        print(f"--- Loaded Input Data from {input_file} ---")
        print(f"Document: {input_data.source_document.title}")
        print(f"Findings to map: {len(input_data.Candidate_findings)}")

    except Exception as e:
        print(f"Error loading or parsing input data: {e}")
        return

    # 2. Call the term_mapper agent
    print("\n--- Calling Term Mapper Agent ---")
    result = create_term_mapper_agent(input_data)

    if result:
        print("\n--- Mapping Result ---")
        # Save output to project root
        output_file = os.path.join(project_root, "mapping_result.json")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=2))
        print(f"Result saved to: {output_file}")
        
        # Display mappings for verification
        for i, finding in enumerate(result.Candidate_findings):
            print(f"\nFinding {i+1}:")
            print(f"  FO: {finding.FO}")
            print(f"  Diagnosis: {finding.nursing_diagnosis.term} (ID: {finding.nursing_diagnosis.ICNP_concept_id})")
            print(f"  Intervention: {finding.intervention.term} (ID: {finding.intervention.ICNP_concept_id})")
            print(f"  Goal: {finding.goal.term} (ID: {finding.goal.ICNP_concept_id})")
    else:
        print("\nMapping failed or returned None.")

if __name__ == "__main__":
    try:
        test_mapping_with_real_data()
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
