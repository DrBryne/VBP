# VBP Workflow: Design Specification (ADK 2.0)

## Objective
The VBP (Veiledende Behandlingsplan) Workflow is an automated clinical synthesis engine designed to process large volumes of nursing literature and generate condensed, evidence-based nursing plans. It bridges the gap between academic research and bedside practice by translating raw literature into standardized ICNP (International Classification for Nursing Practice) terminology.

---

## 🏛️ System Architecture

### 1. The Root Router (`RootRouter`)
The entry point of the ADK application. It inspects incoming messages and session state to dynamically route traffic to either the batch processing pipeline or the conversational chat interface.

### 2. Root Orchestrator (`VbpWorkflowAgent`)
The central brain of the system. It manages the end-to-end lifecycle of a synthesis run:
- **Discovery**: Scans GCS buckets for PDF/XML clinical documents.
- **Parallelism**: Manages a high-concurrency pipeline (default 25 documents) using `asyncio`.
- **State Management**: Tracks progress, success rates, and clinical quality metrics across the entire batch.
- **Consolidation**: Triggers the final semantic merge of findings from all sources.
- **Handover**: Implements a **Link-Based Handover** pattern, saving massive results to GCS and returning lightweight manifests to prevent network timeouts.

### 3. The Document Pipeline
For every document, a four-stage intelligent process is executed:

#### Stage 1: Extraction (`ClinicalExtractor`)
- **Metadata**: Identifies title, year, DOI, and Evidence Level (Knowledge Pyramid).
- **Findings**: Extracts "Clinical Triplets" (Nursing Diagnosis, Intervention, Goal).
- **Read & Point**: Every finding is linked to a verbatim quote using a unique **Sentence ID** index for 100% auditability.

#### Stage 2: Taxonomy Mapping (`ClinicalTaxonomist`)
- **Translator**: Maps natural language findings to official ICNP codes.
- **Memory Optimized**: Uses a **Global Singleton Cache** for the 4,000-term dictionary, reducing RAM usage by 90% during parallel runs.
- **Semantic Logic**: Employs Gemini 3.1 Pro to find the closest clinical match in the standardized hierarchy.

#### Stage 3: Quality Audit (`ClinicalAuditor`)
- **Clinical Gatekeeper**: Rates every finding on **Specificity**, **Actionability**, and **Cohesion**.
- **Thresholding**: Automatically drops findings that fall below a 5.0 quality score or lack direct evidence.

#### Stage 4: Semantic Validation (`Consolidator`)
- **Deduplication**: Uses a **FHIR Terminology Server** (CSIRO) to merge sub-concepts into their parents.
- **Local Fallback**: Utilizes a persistent GCS cache and local Norwegian mappings to ensure speed and accuracy even when APIs are throttled.

### 4. Interactive Synthesis Interface (`ReportChatAgent` & UI)
Once a batch run is complete, the `ReportChatAgent` provides an interactive conversational interface to the resulting synthesis:
- **Streamlit UI**: A frontend application (`frontend/main.py`) that sends `[CHAT]` prefixed messages to the ADK backend to activate chat mode.
- **Dynamic Context Loading**: The agent uses tools (`read_synthesis_report`) to fetch and filter the massive GCS JSON artifacts dynamically, bringing only relevant findings into the context window to prevent token exhaustion.
- **Evidence Verification**: The agent can look up original source document quotes dynamically to verify any synthesized recommendation for the user.

---

## 📊 Outputs & Artifacts

### 1. Standardized Synthesis (JSON)
A machine-readable manifest containing:
- Consolidated Diagnosis-Intervention-Goal clusters.
- All supporting evidence (quotes) mapped by document.
- Scientific trust scores and certainty levels (High/Moderate/Low).

### 2. Clinical Dashboard (HTML)
An automated visual report uploaded to GCS at the end of every run. 
- **Features**: Interactive evidence viewer, color-coded functional areas, and one-click navigation to source citations.

---

## ⚙️ Technical Constraints
- **Concurrency**: Optimized for 10-25 concurrent documents on Vertex AI.
- **Timeouts**: Designed to finish 100-document batches within the 30-minute cloud request limit.
- **Telemetry**: Full OpenTelemetry integration with Cloud Trace, optimized for high-concurrency async detached contexts.
