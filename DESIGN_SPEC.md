# VBP Workflow Design Spec (ADK 2.0)

## 1. Goal
Refactor the VBP (Veiledende Behandlingsplan) workflow to adhere to ADK 2.0 best practices and prepare it for deployment to Agent Engine.

## 2. Architecture Overview

### 2.1 Agents
- **`ResearchAnalyst` (LlmAgent)**: Processes a single GCS document and extracts structured findings using `ModelSchema`.
- **`TermMapper` (SequentialAgent of `IcnpMapper` and `FoClassifier`)**: Maps extracted findings to ICNP terms and classifies them into functional areas (FO).
- **`DocumentProcessor` (SequentialAgent)**: Chains `ResearchAnalyst` and `TermMapper`.
- **`Consolidator` (LlmAgent)**: Synthesizes mapped findings from multiple documents into a single `SynthesisSchema`.
- **`VBPWorkflowAgent` (BaseAgent)**: The root orchestrator that:
    1.  Uses `list_gcs_files` to discover documents.
    2.  Spawns `DocumentProcessor` in parallel for each file (with concurrency control).
    3.  Aggregates results and invokes `Consolidator`.

### 2.2 Shared Resources
- **`app/shared/models.py`**: Pydantic models for all data exchange.
- **`app/shared/tools.py`**: GCS file discovery tool.

### 2.3 Application Entry Point
- **`app/agent.py`**: Defines the `AdkApp` (as `app`) and the `VBPWorkflowAgent`.
- **`app/agent_engine_app.py`**: Defines the `AgentEngineApp` (as `agent_engine`) for deployment.

## 3. Data Flow
1.  **User Input**: `gcs_uri`, `target_group`, `max_files`, `max_concurrency`.
2.  **Workflow Initiation**: `VBPWorkflowAgent` starts.
3.  **Discovery**: `list_gcs_files` returns list of URIs.
4.  **Parallel Processing**:
    - Each URI + `target_group` is sent to `DocumentProcessor`.
    - `ResearchAnalyst` -> `TermMapper`.
5.  **Aggregation**: All `MappedResponseSchema` objects collected.
6.  **Consolidation**: `Consolidator` receives all findings and generates `SynthesisSchema`.
7.  **Final Response**: `SynthesisSchema` returned to user.

## 4. Key Improvements
- Use standard `google.adk.agents.Agent` and workflow agents (`SequentialAgent`, `ParallelAgent`).
- Move orchestration logic into a `BaseAgent` subclass for better encapsulation.
- Leverage ADK's native Pydantic support and state management.
- Standardized file structure for easier deployment and testing.
