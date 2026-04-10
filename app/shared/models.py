"""
Data models for the VBP Workflow.
These schemas define the structural contract between the orchestrator and the LLM agents,
enabling constrained generation and robust validation.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, Field

# --- 1. SHARED CORE TYPES ---

class FunctionalArea(str, Enum):
    """The 12 standard Norwegian Functional Areas (Funksjonsområder) for clinical classification."""
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
    """A standardized clinical term mapped to the ICNP/SNOMED CT taxonomy."""
    term: str = Field(description="The formal Norwegian ICNP term if a match is found, otherwise the original extracted text.")
    ICNP_concept_id: str = Field(description="The official ICNP/SNOMED Concept ID (e.g., '288939007'). Empty if unmapped.")

class Document(BaseModel):
    """Metadata and identification for an analyzed clinical source file."""
    document_id: str | None = Field(default=None, description="Unique internal identifier.")
    source_uri: str = Field(description="Origin path (GCS URI) of the document.")
    title: str = Field(description="The extracted scientific title of the document.")
    publication_year: int = Field(description="Year of publication (0 if unknown).")
    doi: str = Field(description="Digital Object Identifier. Set to 'Not found' if missing.")
    evidence_level: str = Field(description="Quality classification based on the Knowledge Pyramid (e.g., 'Nivå 2: Systematiske oversikter').")
    reasoning_trace: str | None = Field(default=None, description="A step-by-step clinical justification for the selection of this document.")

@dataclass
class WorkflowProgress:
    """Real-time counters for monitoring parallel document processing."""
    completed: int = 0
    success: int = 0
    failed: int = 0
    no_findings: int = 0
    hallucinated_citations: int = 0
    dropped_findings: int = 0
    total_taxonomy_errors: int = 0

# --- 2. RESEARCH ANALYST (Extraction) ---

class ClinicalFinding(BaseModel):
    """A raw clinical finding extracted from a document before terminology mapping."""
    nursing_diagnosis: str = Field(description="The identified clinical problem or nursing diagnosis.")
    intervention: str = Field(description="The proposed nursing action or intervention.")
    goal: Optional[str] = Field(default=None, description="The desired clinical outcome.")
    supporting_sentence_ids: List[str] = Field(description="Ordered list of sentence IDs (e.g., ['S12', 'S13']) from the indexed text that prove this finding.")
    clinical_specificity: int = Field(description="Self-score (1-10): How specific is this finding to the target group? (1=Generic, 10=Highly Condition-Specific)")
    actionability_score: int = Field(description="Self-score (1-10): How concrete and measurable is this intervention? (1=Vague, 10=Fully Actionable)")
    quotes: Optional[List[str]] = Field(default=None, description="Verbatim text resolved from IDs (internal use).")

class MetadataResponse(BaseModel):
    """Schema used by the Metadata Extractor to return document details."""
    source_document: Document

class ClinicalFindingsResponse(BaseModel):
    """Schema used by the Finding Extractor to return identified findings and logic."""
    reasoning_trace: str = Field(description="An explanation of the logic used to select and formulate these findings.")
    candidate_findings: list[ClinicalFinding]

# --- 3. TERM MAPPER (Mapping) ---

class IcnpMapping(BaseModel):
    """Results of mapping a single finding's components to ICNP terms."""
    finding_id: str = Field(description="Links the mapping back to the original extracted finding.")
    nursing_diagnosis: MappedTerm | None = Field(None, description="The ICNP match for the diagnosis.")
    intervention: MappedTerm | None = Field(None, description="The ICNP match for the intervention.")
    goal: MappedTerm | None = Field(None, description="The ICNP match for the goal.")

class IcnpMappingResponse(BaseModel):
    """Batch response from the ICNP mapping agent."""
    results: list[IcnpMapping]

class FunctionalAreaClassification(BaseModel):
    """Assignment of a finding to one of the 12 standard functional areas."""
    finding_id: str = Field(description="Links the classification back to the original finding.")
    FO: FunctionalArea = Field(description="The selected standard category.")

class FunctionalAreaResponse(BaseModel):
    """Batch response from the Functional Area classifier agent."""
    results: List[FunctionalAreaClassification]

# --- 4. AUDITOR (Quality Shield) ---

class AuditorRating(BaseModel):
    """A quality assessment of a clinical triplet (Diagnosis->Intervention->Goal)."""
    finding_id: str = Field(description="Unique identifier matching the input data.")
    specificity_score: int = Field(description="Score (1-10): Generic nursing (1) vs specialized care (10).")
    actionability_score: int = Field(description="Score (1-10): Vague instructions (1) vs precise/measurable (10).")
    cohesion_score: int = Field(description="Score (1-10): Logical disconnect (1) vs logically consistent clinical chain (10).")
    auditor_comment: str = Field(description="A brief (one-sentence) justification for the scores.")

class AuditorResponse(BaseModel):
    """Batch response from the Clinical Auditor agent."""
    results: List[AuditorRating]

# --- 5. INTERNAL WORKFLOW STATE ---

class ProcessedFinding(ClinicalFinding):
    """The enriched version of a finding containing both raw text and formal terminology mappings."""
    finding_id: str
    document_id: str
    mapped_nursing_diagnosis: MappedTerm
    mapped_intervention: MappedTerm
    mapped_goal: MappedTerm
    FO: FunctionalArea
    auditor_rating: Optional[AuditorRating] = None
    weighted_quality_score: float = 0.0

class ProcessedDocument(BaseModel):
    """The complete processing result for a single document, containing its findings and metadata."""
    source_document: Document
    mapped_findings: list[ProcessedFinding]

# --- 5. CONSOLIDATOR (Synthesis) ---

class Evidence(BaseModel):
    """Grouped evidence for a synthesized finding, linked to its source document."""
    document_id: str = Field(description="The ID of the source document.")
    quotes: list[str] = Field(description="List of context-padded verbatim quotes supporting the finding.")

class SynthesizedFinding(BaseModel):
    """A high-level clinical finding consolidated across multiple source documents."""
    nursing_diagnosis: MappedTerm
    intervention: MappedTerm
    goal: MappedTerm
    FO: FunctionalArea = Field(description="The clinical category (Functional Area).")
    avg_specificity: float = Field(description="Average specificity score from the auditor.")
    avg_actionability: float = Field(description="Average actionability score from the auditor.")
    avg_cohesion: float = Field(description="Average logical cohesion score from the auditor.")
    trust_score: float = Field(description="A composite score based on evidence frequency and source level.")
    supporting_evidence: List[Evidence] = Field(description="The specific verbatim evidence gathered from various sources.")

class ExcludedDocument(BaseModel):
    """A record of a document that was processed but rejected from the final synthesis."""
    source_uri: str = Field(description="GCS path of the document.")
    title: str = Field(description="Document title or filename.")
    justification: str = Field(description="The specific reason for exclusion (e.g., lack of findings, invalid citations).")

class ExecutionSummary(BaseModel):
    """Comprehensive operational and quality metrics for a workflow execution run."""
    target_group: str = Field(description="The scope of the analysis.")
    source_uri: str = Field(description="The GCS path that was scanned.")
    total_files_in_uri: int = Field(description="Total files discovered in the bucket.")
    processed_files_count: int = Field(description="Total files analyzed in this run.")
    successful_files_count: int = Field(description="Documents that contributed valid evidence.")
    excluded_files_count: int = Field(description="Documents that were analyzed but omitted.")
    total_synthesized_findings: int = Field(description="Total number of unique clinical findings consolidated.")
    total_hallucinated_citations: int = Field(description="Count of invalid sentence IDs corrected during resolution.")
    total_taxonomy_errors: int = Field(description="Count of hallucinated ICNP IDs corrected during validation.")
    total_dropped_findings: int = Field(description="Count of findings removed due to lack of valid evidence.")
    execution_start_time: datetime = Field(description="Workflow start timestamp.")
    execution_end_time: datetime = Field(description="Workflow completion timestamp.")

class SynthesisResponse(BaseModel):
    """The final structured clinical report generated by the VBP Workflow."""
    execution_summary: ExecutionSummary = Field(description="Metadata and performance metrics for the run.")
    synthesized_findings: list[SynthesizedFinding] = Field(description="The core consolidated clinical evidence.")
    source_documents: list[Document] = Field(description="Details of all documents that provided successful findings.")
    excluded_documents: list[ExcludedDocument] = Field(default_factory=list, description="Audit trail of documents omitted from the synthesis.")
