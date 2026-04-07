from typing import List, Optional
from pydantic import BaseModel, Field

# --- Research Analyst Schemas ---

class SourceDocumentBase(BaseModel):
    title: str = Field(description="Tittelen på den vitenskapelige artikkelen.")
    publication_year: int = Field(description="Årstall for publikasjon. Sett til 0 hvis ikke funnet.")
    doi: str = Field(description="DOI-nummeret. Sett til 'Ikke funnet' hvis det ikke oppgis.")
    evidence_level: str = Field(description="Klassifisering basert på Kunnskapspyramiden (f.eks. 'Nivå 1: Studier').")
    reasoning_trace: List[str] = Field(description="En detaljert, trinnvis oversikt over hvordan du evaluerte forespørselen.")

class FindingBase(BaseModel):
    nursing_diagnosis: str = Field(description="Den utledede sykepleiediagnosen.")
    intervention: str = Field(description="Sykepleieintervensjon.")
    goal: str = Field(description="Mål for intervensjonen.")
    quotes: List[str] = Field(description="Liste over sitater som understøtter funnet.")

class ModelSchema(BaseModel):
    """The schema passed to the LLM for clinical extraction."""
    source_document: SourceDocumentBase
    Candidate_findings: List[FindingBase]

class SourceDocumentEnriched(SourceDocumentBase):
    document_id: str = Field(description="Unik identifikator for kildedokumentet.")

class FindingEnriched(FindingBase):
    document_id: str = Field(description="Unik identifikator for kildedokumentet.")

class ResponseSchema(BaseModel):
    """The final schema returned by the agent, enriched with IDs and reasoning."""
    source_document: SourceDocumentEnriched
    Candidate_findings: List[FindingEnriched]

# --- Term Mapper Schemas ---

class MappingBase(BaseModel):
    term: str = Field(description="Den norske ICNP-termen (Term_NO) hvis match finnes, ellers den opprinnelige teksten.")
    ICNP_concept_id: str = Field(description="Den tilhørende ICNP-ID-en (Concept Id) hvis match finnes, ellers tom streng.")

class NursingDiagnosisMapping(MappingBase):
    pass

class InterventionMapping(MappingBase):
    pass

class GoalMapping(MappingBase):
    pass

class MappedFinding(BaseModel):
    nursing_diagnosis: NursingDiagnosisMapping
    intervention: InterventionMapping
    goal: GoalMapping
    FO: str = Field(description="Funksjonsområde (1-12) for dette funnet.")
    quotes: List[str] = Field(description="Liste over sitater som understøtter funnet.")
    document_id: str = Field(description="Unik identifikator for kildedokumentet.")

class MappedResponseSchema(BaseModel):
    """The final schema returned by the term_mapper agent."""
    source_document: SourceDocumentEnriched
    Candidate_findings: List[MappedFinding]

# --- LLM Optimization Schemas ---

class SimplifiedFinding(BaseModel):
    """A lean finding structure used as input for the LLM mapping task."""
    finding_id: str = Field(description="Unik identifikator for å koble resultatet tilbake.")
    nursing_diagnosis: str = Field(description="Den utledede sykepleiediagnosen.")
    intervention: str = Field(description="Sykepleieintervensjon.")
    goal: str = Field(description="Mål for intervensjonen.")

class FindingMappingResult(BaseModel):
    """The structured result returned by the LLM for a single finding."""
    finding_id: str = Field(description="Unik identifikator som matcher inngangsdataene.")
    nursing_diagnosis: Optional[NursingDiagnosisMapping] = Field(None, description="Mappet resultat for diagnose.")
    intervention: Optional[InterventionMapping] = Field(None, description="Mappet resultat for intervensjon.")
    goal: Optional[GoalMapping] = Field(None, description="Mappet resultat for mål.")

class LLMMappingResponse(BaseModel):
    """The list of mapping results returned by the LLM."""
    results: List[FindingMappingResult]

class FOClassificationResult(BaseModel):
    """The structured result for FO classification of a single finding."""
    finding_id: str = Field(description="Unik identifikator som matcher inngangsdataene.")
    FO: str = Field(description="Det valgte funksjonsområdet (navn og nummer).")

class LLMFOClassificationResponse(BaseModel):
    """The list of FO classification results returned by the LLM."""
    results: List[FOClassificationResult]

# --- Consolidator and Workflow Schemas ---

class ConsolidatedResponseSchema(BaseModel):
    """A collection of all mapped responses from multiple documents."""
    all_mapped_findings: List[MappedFinding] = Field(description="En samling av alle mappede funn fra alle kildedokumenter.")
    source_documents: List[SourceDocumentEnriched] = Field(description="En liste over alle kildedokumentene som ble analysert.")

class EvidenceQuote(BaseModel):
    """Associates specific quotes with their source document."""
    document_id: str = Field(description="ID til dokumentet sitatene er hentet fra.")
    quotes: List[str] = Field(description="Ordrette sitater fra dette spesifikke kildedokumentet som understøtter funnet.")

class SynthesisFinding(BaseModel):
    """A synthesized, deduplicated clinical finding."""
    nursing_diagnosis: NursingDiagnosisMapping
    intervention: InterventionMapping
    goal: GoalMapping
    FO: str = Field(description="Funksjonsområde (1-12).")
    evidence_summary: str = Field(description="Et kort sammendrag av evidensen for dette funnet på tvers av artikler.")
    supporting_evidence: List[EvidenceQuote] = Field(description="Dokumentasjon og spesifikke sitater fra kildene som støtter dette funnet.")

class SynthesisSchema(BaseModel):
    """The final, clean output after consolidation and quality control."""
    target_group: str = Field(description="Målgruppen analysen gjelder for.")
    synthesized_findings: List[SynthesisFinding] = Field(description="De konsoliderte og kvalitetssikrede kliniske funnene.")
    total_documents_processed: int
    quality_notes: str = Field(description="Notater fra kvalitetskontrollen (f.eks. om motstridende funn eller datakvalitet).")
    source_documents: Optional[List[SourceDocumentEnriched]] = Field(default=None, description="En liste over alle kildedokumentene som ble analysert.")
