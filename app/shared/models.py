from typing import List, Optional, Dict, Literal
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from pydantic import BaseModel, Field

# --- 1. SHARED CORE TYPES ---
class FunctionalArea(str, Enum):
    FO1 = "1. Kommunikasjon/sanser"
    FO2 = "2. Kunnskap/utvikling/psykisk"
    FO3 = "3. Respirasjon/sirkulasjon"
    FO4 = "4. Ernæring/væske/elektrolyttbalanse"
    FO5 = "5. Eliminasjon"
    FO6 = "6. Hud/vev/sår"
    FO7 = "7. Aktivitet/funksjonsstatus"
    FO8 = "8. Smerte/søvn/hvile/velvære"
    FO9 = "9. Seksualitet/reproduksjon"
    FO10 = "10. Sosiale forhold/miljø"
    FO11 = "11. Åndelig/kulturelt/livsavslutning"
    FO12 = "12. Annet/legedelegerte aktiviteter"

class MappedTerm(BaseModel):
    term: str = Field(description="The Norwegian ICNP term (Term_NO) if a match is found, otherwise the original text.")
    ICNP_concept_id: str = Field(description="The corresponding ICNP ID (Concept Id) if a match is found, otherwise an empty string.")

class Document(BaseModel):
    document_id: Optional[str] = Field(default=None, description="Internal ID (not filled by LLM).")
    source_uri: str = Field(description="The GCS URI or local path to the source document.")
    title: str = Field(description="The title of the scientific document.")
    publication_year: int = Field(description="Year of publication. Set to 0 if not found.")
    doi: str = Field(description="The DOI number. Set to 'Not found' if not provided.")
    evidence_level: str = Field(description="Classification based on the Knowledge Pyramid (e.g., 'Level 1: Studies').")
    reasoning_trace: Optional[str] = Field(default=None, description="A step-by-step explanation of the clinical selection process for this document.")

@dataclass
class WorkflowProgress:
    completed: int = 0
    success: int = 0
    failed: int = 0
    no_findings: int = 0
    hallucinated_citations: int = 0
    dropped_findings: int = 0
    total_taxonomy_errors: int = 0

# --- 2. RESEARCH ANALYST (Extraction) ---
class ClinicalFinding(BaseModel):
    nursing_diagnosis: str = Field(description="The derived nursing diagnosis.")
    intervention: str = Field(description="Nursing intervention.")
    goal: Optional[str] = Field(default=None, description="Goal for the intervention.")
    supporting_sentence_ids: List[str] = Field(description="List of exact sentence IDs (e.g., ['S12', 'S15']) from the indexed text that support this finding.")
    quotes: Optional[List[str]] = Field(default=None, description="Verbatim quotes resolved from sentence IDs (internal use).")

class MetadataResponse(BaseModel):
    """Schema for the specialized Metadata Extractor agent."""
    source_document: Document

class ClinicalFindingsResponse(BaseModel):
    """Schema for the specialized Finding Extractor agent."""
    reasoning_trace: str = Field(description="A step-by-step explanation of the selection process.")
    candidate_findings: List[ClinicalFinding]

# --- 3. TERM MAPPER (LLM outputs) ---
class IcnpMapping(BaseModel):
    finding_id: str = Field(description="Unique identifier matching the input data.")
    nursing_diagnosis: Optional[MappedTerm] = Field(None, description="Mapped result for diagnosis.")
    intervention: Optional[MappedTerm] = Field(None, description="Mapped result for intervention.")
    goal: Optional[MappedTerm] = Field(None, description="Mapped result for goal.")

class IcnpMappingResponse(BaseModel):
    results: List[IcnpMapping]

class FunctionalAreaClassification(BaseModel):
    finding_id: str = Field(description="Unique identifier matching the input data.")
    FO: FunctionalArea = Field(description="The selected functional area (name and number).")

class FunctionalAreaResponse(BaseModel):
    results: List[FunctionalAreaClassification]

# --- 4. INTERNAL WORKFLOW STATE ---
class ProcessedFinding(ClinicalFinding):
    """The unified finding object containing raw text, mappings, and IDs."""
    finding_id: str
    document_id: str
    mapped_nursing_diagnosis: MappedTerm
    mapped_intervention: MappedTerm
    mapped_goal: MappedTerm
    FO: FunctionalArea

class ProcessedDocument(BaseModel):
    """Result from processing a single document through Analyst and Mapper."""
    source_document: Document
    mapped_findings: List[ProcessedFinding]

# --- 5. CONSOLIDATOR ---
class Evidence(BaseModel):
    document_id: str = Field(description="ID of the document the quotes are taken from.")
    quotes: List[str] = Field(description="Verbatim quotes from this source document supporting the finding.")

class SynthesizedFinding(BaseModel):
    nursing_diagnosis: MappedTerm
    intervention: MappedTerm
    goal: MappedTerm
    FO: FunctionalArea = Field(description="Functional Area (1-12).")
    supporting_evidence: List[Evidence] = Field(description="Specific quotes from sources supporting the finding.")

class ExcludedDocument(BaseModel):
    source_uri: str = Field(description="The GCS URI or local path to the source document.")
    title: str = Field(description="The title of the document, or the filename if the title could not be extracted.")
    justification: str = Field(description="The justification for why the document was excluded from the clinical synthesis.")

class ExecutionSummary(BaseModel):
    target_group: str = Field(description="The target group the analysis applies to.")
    source_uri: str = Field(description="The GCS URI or local path that was scanned for documents.")
    total_files_in_uri: int = Field(description="Total number of files discovered in the source URI.")
    processed_files_count: int = Field(description="Total number of files actually processed (limited by max_files).")
    successful_files_count: int = Field(description="Number of files that yielded clinical findings.")
    excluded_files_count: int = Field(description="Number of files excluded due to lack of findings or errors.")
    total_synthesized_findings: int = Field(description="Total number of unique clinical findings consolidated.")
    total_hallucinated_citations: int = Field(description="Total number of hallucinated sentence IDs returned by the LLM that did not exist in the document.")
    total_taxonomy_errors: int = Field(description="Total number of hallucinated ICNP IDs or invalid Functional Areas caught and corrected.")
    total_dropped_findings: int = Field(description="Total number of findings that were dropped due to no valid quotes.")
    execution_start_time: datetime = Field(description="Timestamp when the workflow execution started.")
    execution_end_time: datetime = Field(description="Timestamp when the workflow execution completed.")

class SynthesisResponse(BaseModel):
    execution_summary: ExecutionSummary = Field(description="Summary of the workflow execution and descriptive statistics.")
    synthesized_findings: List[SynthesizedFinding] = Field(description="The consolidated clinical findings.")
    source_documents: List[Document] = Field(description="List of all source documents successfully analyzed.")
    excluded_documents: List[ExcludedDocument] = Field(default_factory=list, description="List of documents that were processed but excluded from the final findings.")
