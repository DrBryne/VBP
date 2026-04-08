from typing import List, Optional
from pydantic import BaseModel, Field

# --- 1. SHARED CORE TYPES ---
class MappedTerm(BaseModel):
    term: str = Field(description="Den norske ICNP-termen (Term_NO) hvis match finnes, ellers den opprinnelige teksten.")
    ICNP_concept_id: str = Field(description="Den tilhørende ICNP-ID-en (Concept Id) hvis match finnes, ellers tom streng.")

class Document(BaseModel):
    document_id: Optional[str] = Field(default=None, description="Intern ID (fylles ikke ut av LLM).")
    title: str = Field(description="Tittelen på den vitenskapelige artikkelen.")
    publication_year: int = Field(description="Årstall for publikasjon. Sett til 0 hvis ikke funnet.")
    doi: str = Field(description="DOI-nummeret. Sett til 'Ikke funnet' hvis det ikke oppgis.")
    evidence_level: str = Field(description="Klassifisering basert på Kunnskapspyramiden (f.eks. 'Nivå 1: Studier').")
    reasoning_trace: List[str] = Field(description="En detaljert, trinnvis oversikt over hvordan du evaluerte forespørselen.")

# --- 2. RESEARCH ANALYST (Extraction) ---
class ExtractedFinding(BaseModel):
    nursing_diagnosis: str = Field(description="Den utledede sykepleiediagnosen.")
    intervention: str = Field(description="Sykepleieintervensjon.")
    goal: str = Field(description="Mål for intervensjonen.")
    quotes: List[str] = Field(description="Liste over sitater som understøtter funnet.")

class ExtractionResponse(BaseModel):
    """The schema passed to the LLM for clinical extraction."""
    source_document: Document
    Candidate_findings: List[ExtractedFinding]

# --- 3. TERM MAPPER (LLM outputs) ---
class TermMapping(BaseModel):
    finding_id: str = Field(description="Unik identifikator som matcher inngangsdataene.")
    nursing_diagnosis: Optional[MappedTerm] = Field(None, description="Mappet resultat for diagnose.")
    intervention: Optional[MappedTerm] = Field(None, description="Mappet resultat for intervensjon.")
    goal: Optional[MappedTerm] = Field(None, description="Mappet resultat for mål.")

class TermMappingResponse(BaseModel):
    results: List[TermMapping]

class FOClassification(BaseModel):
    finding_id: str = Field(description="Unik identifikator som matcher inngangsdataene.")
    FO: str = Field(description="Det valgte funksjonsområdet (navn og nummer).")

class FOClassificationResponse(BaseModel):
    results: List[FOClassification]

# --- 4. INTERNAL WORKFLOW STATE ---
class ProcessedFinding(ExtractedFinding):
    """The unified finding object containing raw text, mappings, and IDs."""
    finding_id: str
    document_id: str
    mapped_nursing_diagnosis: MappedTerm
    mapped_intervention: MappedTerm
    mapped_goal: MappedTerm
    FO: str

class ProcessedDocument(BaseModel):
    """Result from processing a single document through Analyst and Mapper."""
    source_document: Document
    mapped_findings: List[ProcessedFinding]

# --- 5. CONSOLIDATOR ---
class Evidence(BaseModel):
    document_id: str = Field(description="ID til dokumentet sitatene er hentet fra.")
    quotes: List[str] = Field(description="Ordrette sitater fra dette kildedokumentet som understøtter funnet.")

class SynthesizedFinding(BaseModel):
    nursing_diagnosis: MappedTerm
    intervention: MappedTerm
    goal: MappedTerm
    FO: str = Field(description="Funksjonsområde (1-12).")
    evidence_summary: str = Field(description="Kort sammendrag av evidensen for dette funnet på tvers av artikler.")
    supporting_evidence: List[Evidence] = Field(description="Spesifikke sitater fra kildene som støtter funnet.")

class SynthesisResponse(BaseModel):
    target_group: str = Field(description="Målgruppen analysen gjelder for.")
    synthesized_findings: List[SynthesizedFinding] = Field(description="De konsoliderte kliniske funnene.")
    total_documents_processed: int
    quality_notes: str = Field(description="Notater fra kvalitetskontrollen.")
    source_documents: List[Document] = Field(description="Liste over alle kildedokumentene som ble analysert.")
