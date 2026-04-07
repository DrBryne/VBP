import os
import json
import asyncio
from typing import List, Optional
from vertexai.agent_engines import AdkApp

# --- Pydantic Schemas ---
import sys
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "agents"))

from shared.models import (
    MappedResponseSchema, 
    ConsolidatedResponseSchema, 
    SynthesisSchema
)

# --- Agent Components ---
from shared.tools import list_gcs_files
from research_analyst.agent import get_research_analyst_agent, analyze_document_with_app
from term_mapper.agent import get_term_mapper_agents, map_terms_with_apps
from consolidator.agent import get_consolidator_agent, consolidate_findings_with_app

class VBPWorkflow:
    def __init__(self, project_id: str, location: str):
        self.project_id = project_id
        
        # Initialize Agents
        self.analyst_agent = get_research_analyst_agent()
        self.mapping_agent, self.fo_agent = get_term_mapper_agents()
        self.consolidator_agent = get_consolidator_agent()
        
        # Initialize Apps
        self.analyst_app = AdkApp(agent=self.analyst_agent)
        self.mapping_app = AdkApp(agent=self.mapping_agent)
        self.fo_app = AdkApp(agent=self.fo_agent)
        self.consolidator_app = AdkApp(agent=self.consolidator_agent)

    def _get_files(self, gcs_uri: str, max_files: Optional[int] = None) -> List[str]:
        """Uses the standardized tool to discover files."""
        files = list_gcs_files(gcs_uri=gcs_uri, project_id=self.project_id)
        if max_files:
            return files[:max_files]
        return files

    async def process_single_document(self, target_group: str, gcs_uri: str) -> Optional[MappedResponseSchema]:
        """Runs the analyst and term mapper pipeline for a single file using AdkApps."""
        try:
            # 1. Research Analyst
            analysis = await analyze_document_with_app(self.analyst_app, target_group, gcs_uri)
            if not analysis:
                print(f"Failed to analyze document: {gcs_uri}")
                return None
            
            # 2. Term Mapper
            mapped = await map_terms_with_apps(self.mapping_app, self.fo_app, analysis)
            if not mapped:
                print(f"Failed to map terms for document: {gcs_uri}")
                return None
                
            return mapped
        except Exception as e:
            print(f"Exception while processing document {gcs_uri}: {e}")
            return None

    async def run(self, gcs_uri: str, target_group: str, max_files: Optional[int] = None, max_concurrency: int = 10) -> Optional[SynthesisSchema]:
        """Main entry point for the workflow with concurrency control."""
        # 1. Discovery
        files = self._get_files(gcs_uri, max_files=max_files)
        print(f"Found {len(files)} files to process (Limit: {max_files}). Concurrency: {max_concurrency}")

        # 2. Controlled Parallel Processing (Analyst -> Term Mapper)
        semaphore = asyncio.Semaphore(max_concurrency)

        async def sem_process(f):
            async with semaphore:
                return await self.process_single_document(target_group, f)

        tasks = [sem_process(f) for f in files]
        mapped_responses = await asyncio.gather(*tasks)
        
        # Filter out failures
        successful_responses = [r for r in mapped_responses if r is not None]
        print(f"Successfully processed {len(successful_responses)} out of {len(files)} documents.")

        if not successful_responses:
            return None

        # 3. Consolidation
        # Prepare ConsolidatedResponseSchema
        all_findings = []
        all_docs = []
        for resp in successful_responses:
            all_findings.extend(resp.Candidate_findings)
            all_docs.append(resp.source_document)
        
        consolidated_data = ConsolidatedResponseSchema(
            all_mapped_findings=all_findings,
            source_documents=all_docs
        )

        # 4. Consolidator Agent
        synthesis = await consolidate_findings_with_app(self.consolidator_app, target_group, consolidated_data)
        
        if synthesis:
            # The LLM often miscounts large datasets, so we forcefully set the accurate count
            synthesis.total_documents_processed = len(successful_responses)
            synthesis.source_documents = all_docs
            
        return synthesis

# Define the entry point for deployment or local execution
def create_vbp_workflow_agent(project_id: str, location: str):
    workflow = VBPWorkflow(project_id=project_id, location=location)

    async def workflow_task(gcs_uri: str, target_group: str, max_files: Optional[int] = None, max_concurrency: int = 10) -> str:
        """The function that Vertex AI Agent Engine will execute."""
        result = await workflow.run(gcs_uri, target_group, max_files=max_files, max_concurrency=max_concurrency)
        if result:
            return result.model_dump_json(indent=2)
        return json.dumps({"error": "Workflow failed to produce a result."})
    
    return workflow_task

if __name__ == "__main__":
    pass
