import os
import asyncio
import json
import uuid
import random
from typing import AsyncGenerator, List, Optional

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.adk.apps import App
from google.genai import types
from google.adk.sessions import InMemorySessionService

from app.shared.models import (
    ExtractionResponse,
    ProcessedDocument,
    ProcessedFinding,
    MappedTerm,
    TermMappingResponse,
    FOClassificationResponse
)
from app.shared.tools import list_gcs_files
from app.agents.research_analyst.agent import create_research_analyst
from app.agents.term_mapper.agent import create_term_mapper
from app.agents.consolidator.agent import create_consolidator

class VBPWorkflowAgent(BaseAgent):
    """
    Root orchestrator for the VBP (Veiledende Behandlingsplan) Workflow.
    This BaseAgent implements a data-driven parallel workflow with isolated contexts.
    """
    def __init__(self, name: str = "vbp_workflow_agent"):
        super().__init__(name=name)
        self._analyst_agent = create_research_analyst()
        self._mapper_agent = create_term_mapper()
        self._consolidator_agent = create_consolidator()

    @property
    def analyst(self):
        return self._analyst_agent

    @property
    def mapper(self):
        return self._mapper_agent

    @property
    def consolidator(self):
        return self._consolidator_agent

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        gcs_uri = ctx.session.state.get("gcs_uri")
        target_group = ctx.session.state.get("target_group")
        max_files = ctx.session.state.get("max_files")
        max_concurrency = ctx.session.state.get("max_concurrency", 10)
        
        # Try to extract from the incoming message if not in state
        if not gcs_uri or not target_group:
            msg_text = ""
            for msg in ctx.session.events:
                if msg.content and msg.content.role == "user" and msg.content.parts:
                    msg_text = msg.content.parts[0].text
                    break
            
            if msg_text:
                try:
                    import json
                    config = json.loads(msg_text)
                    gcs_uri = config.get("gcs_uri", gcs_uri)
                    target_group = config.get("target_group", target_group)
                    max_files = config.get("max_files", max_files)
                    max_concurrency = config.get("max_concurrency", max_concurrency)
                except Exception:
                    pass

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")

        if not gcs_uri or not target_group:
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part.from_text(text="Missing required configuration. Please set 'gcs_uri' and 'target_group' in the session state.")])
            )
            return

        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Starting discovery in: {gcs_uri}")]))
        try:
            files = list_gcs_files(gcs_uri, project_id)
            if max_files:
                files = files[:max_files]
        except Exception as e:
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Discovery failed: {e}")]))
            return

        if not files:
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No files found to process.")]))
            return

        total_files = len(files)
        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Processing {total_files} documents in parallel (limit: {max_concurrency})...")]))

        semaphore = asyncio.Semaphore(max_concurrency)
        progress_queue = asyncio.Queue()
        
        # Create an ephemeral session service for parallel execution isolation
        ephemeral_session_service = InMemorySessionService()

        async def process_single_document(uri: str) -> Optional[ProcessedDocument]:
            filename = uri.split("/")[-1]
            async with semaphore:
                try:
                    await progress_queue.put(f"START: {filename}")
                    # Staggered start to prevent massive initial burst of API calls
                    await asyncio.sleep(random.uniform(0.1, 5.0))
                    
                    # 1. Isolate the execution context
                    doc_session_id = str(uuid.uuid4())
                    doc_session = await ephemeral_session_service.create_session(
                        app_name="vbp_workflow", 
                        user_id="system", 
                        session_id=doc_session_id
                    )
                    doc_ctx = ctx.model_copy(
                        update={
                            "session": doc_session,
                            "session_service": ephemeral_session_service,
                            "invocation_id": str(uuid.uuid4())
                        }
                    )
                    
                    # 2. STEP A: Research Analysis
                    import mimetypes
                    from google.cloud import storage
                    
                    mime_type, _ = mimetypes.guess_type(uri)
                    
                    if mime_type == "application/pdf":
                        parts = [
                            types.Part.from_uri(file_uri=uri, mime_type=mime_type),
                            types.Part.from_text(text=f"Bruksområde: {target_group}\n\nAnalyser den vedlagte artikkelen.")
                        ]
                    else:
                        # Vertex AI Gemini API rejects XML/text files via GCS URI, so we must download and pass inline
                        parts_uri = uri[5:].split("/", 1)
                        bucket_name = parts_uri[0]
                        blob_name = parts_uri[1]
                        
                        storage_client = storage.Client(project=project_id)
                        bucket = storage_client.bucket(bucket_name)
                        blob = bucket.blob(blob_name)
                        file_bytes = blob.download_as_bytes()
                        
                        file_text = file_bytes.decode('utf-8', errors='replace')
                        
                        parts = [
                            types.Part.from_text(text=f"Dokumentinnhold:\n{file_text}"),
                            types.Part.from_text(text=f"Bruksområde: {target_group}\n\nAnalyser den vedlagte artikkelen.")
                        ]
                        
                    analyst_msg = types.Content(
                        role="user",
                        parts=parts
                    )
                    
                    doc_ctx.session.events.append(Event(author="system", content=analyst_msg))
                    
                    last_text = None
                    async for ev in self.analyst.run_async(doc_ctx):
                        if ev.content and ev.content.parts:
                             last_text = ev.content.parts[0].text
                    
                    await progress_queue.put(f"ANALYST DONE: {filename}")
                        
                    # Extract the automatically parsed Pydantic object (thanks to output_key)
                    analyst_data: ExtractionResponse = doc_ctx.session.state.get("analyst_results")
                    
                    if not analyst_data and last_text:
                        try:
                            clean_text = last_text.strip()
                            if clean_text.startswith("```json"):
                                clean_text = clean_text[7:]
                            if clean_text.endswith("```"):
                                clean_text = clean_text[:-3]
                            
                            import json
                            data_dict = json.loads(clean_text)
                            analyst_data = ExtractionResponse.model_validate(data_dict)
                        except Exception as e:
                            await progress_queue.put(f"ANALYST PARSE ERROR: {filename} ({e})")
                            pass
                    
                    if not analyst_data or not analyst_data.Candidate_findings:
                        await progress_queue.put(f"NO FINDINGS: {filename}")
                        return None
                        
                    # Assign an internal ID to the source document if it lacks one
                    doc_id = analyst_data.source_document.document_id or str(uuid.uuid4())
                    analyst_data.source_document.document_id = doc_id

                    # 3. Pre-process findings for the Mapper (dynamically create lean JSON)
                    lean_findings = []
                    finding_map = {}
                    for finding in analyst_data.Candidate_findings:
                        internal_id = str(uuid.uuid4())
                        finding_map[internal_id] = finding
                        lean_findings.append({
                            "finding_id": internal_id,
                            "nursing_diagnosis": finding.nursing_diagnosis,
                            "intervention": finding.intervention,
                            "goal": finding.goal
                        })

                    mapper_input = json.dumps(lean_findings)
                    
                    # 4. STEP B: Mapping & Classification (ParallelAgent)
                    mapper_msg = types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=mapper_input)]
                    )
                    
                    doc_ctx.session.events.append(Event(author="system", content=mapper_msg))
                    
                    try:
                        async for ev in self.mapper.run_async(doc_ctx):
                            if ev.is_final_response() and ev.content and ev.content.parts:
                                text = ev.content.parts[0].text
                                try:
                                    clean_text = text.strip()
                                    if clean_text.startswith("```json"):
                                        clean_text = clean_text[7:]
                                    if clean_text.endswith("```"):
                                        clean_text = clean_text[:-3]
                                    
                                    import json
                                    data_dict = json.loads(clean_text)
                                    if ev.author == "icnp_mapper":
                                        doc_ctx.session.state["icnp_results"] = TermMappingResponse.model_validate(data_dict)
                                    elif ev.author == "fo_classifier":
                                        doc_ctx.session.state["fo_results"] = FOClassificationResponse.model_validate(data_dict)
                                except Exception as e:
                                    await progress_queue.put(f"MAPPER PARSE ERROR ({ev.author}): {filename} ({e})")
                                    # Still fail if parsing was critical
                                    pass
                    except Exception as e:
                        await progress_queue.put(f"MAPPER CRITICAL ERROR: {filename} ({e})")
                        return None
                        
                    # Extract mapped outputs
                    icnp_res: TermMappingResponse = doc_ctx.session.state.get("icnp_results")
                    fo_res: FOClassificationResponse = doc_ctx.session.state.get("fo_results")
                    
                    missing = []
                    if not icnp_res: missing.append("icnp_results")
                    if not fo_res: missing.append("fo_results")
                    
                    if missing:
                        await progress_queue.put(f"MAPPER INCOMPLETE ({', '.join(missing)}): {filename}")
                        return None
                        
                    # 5. Merge Logic: Reconstruct ProcessedDocument
                    icnp_lookup = {res.finding_id: res for res in icnp_res.results}
                    fo_lookup = {res.finding_id: res.FO for res in fo_res.results}
                    
                    processed_findings = []
                    for f_id, original in finding_map.items():
                        map_res = icnp_lookup.get(f_id)
                        fo_val = fo_lookup.get(f_id, "12. Annet/legedelegerte aktiviteter")
                        
                        def resolve(orig_val, mapping):
                            if mapping and mapping.term:
                                return MappedTerm(term=mapping.term, ICNP_concept_id=mapping.ICNP_concept_id)
                            return MappedTerm(term=orig_val, ICNP_concept_id="")

                        processed_finding = ProcessedFinding(
                            finding_id=f_id,
                            document_id=doc_id,
                            nursing_diagnosis=original.nursing_diagnosis,
                            intervention=original.intervention,
                            goal=original.goal,
                            quotes=original.quotes,
                            mapped_nursing_diagnosis=resolve(original.nursing_diagnosis, map_res.nursing_diagnosis if map_res else None),
                            mapped_intervention=resolve(original.intervention, map_res.intervention if map_res else None),
                            mapped_goal=resolve(original.goal, map_res.goal if map_res else None),
                            FO=fo_val
                        )
                        processed_findings.append(processed_finding)

                    await progress_queue.put(f"SUCCESS: {filename} ({len(processed_findings)} findings)")
                    return ProcessedDocument(
                        source_document=analyst_data.source_document,
                        mapped_findings=processed_findings
                    )
                except Exception as doc_e:
                    await progress_queue.put(f"DOC CRITICAL ERROR: {filename} ({doc_e})")
                    return None

        # Create tasks and a monitor for the queue
        tasks = [process_single_document(f) for f in files]
        
        async def run_gather():
            return await asyncio.gather(*tasks)
            
        gather_task = asyncio.create_task(run_gather())
        
        completed_count = 0
        success_count = 0
        while not gather_task.done() or not progress_queue.empty():
            try:
                # Wait for a progress message or task completion
                msg = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"[Progress] {msg}")]))
                
                if msg.startswith("SUCCESS:") or msg.startswith("NO FINDINGS:") or "INCOMPLETE" in msg or "ERROR" in msg:
                    completed_count += 1
                    if msg.startswith("SUCCESS:"):
                        success_count += 1
                    
                    if completed_count % 5 == 0 or completed_count == total_files:
                         yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"*** Overall Progress: {completed_count}/{total_files} documents processed ({success_count} with findings) ***")]))
                
                progress_queue.task_done()
            except asyncio.TimeoutError:
                continue

        mapped_results = await gather_task
        successful_results = [r for r in mapped_results if r is not None]

        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text=f"Processed {len(successful_results)}/{len(files)} documents successfully.")]))

        if not successful_results:
            yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="No data available for synthesis.")]))
            return

        all_findings = []
        all_docs = []
        for r in successful_results:
            all_findings.extend(r.mapped_findings)
            all_docs.append(r.source_document)
        
        # We don't really need ConsolidatedResponseSchema Pydantic model anymore. 
        # We can just build the JSON directly.
        consolidated_payload = {
            "all_mapped_findings": [f.model_dump() for f in all_findings],
            "source_documents": [d.model_dump() for d in all_docs]
        }

        consolidator_msg = types.Content(
            role="user",
            parts=[types.Part.from_text(text=f"Målgruppe: {target_group}\n\nSyntetiser disse funnene:\n{json.dumps(consolidated_payload)}")]
        )

        yield Event(author=self.name, content=types.Content(parts=[types.Part.from_text(text="Starting final synthesis of all collected findings...")]))
        ctx.session.events.append(Event(author="system", content=consolidator_msg))
        async for ev in self.consolidator.run_async(ctx):
            yield ev

# --- ADK Application Definition ---

root_agent = VBPWorkflowAgent()

app = App(
    name="vbp_workflow",
    root_agent=root_agent,
)
