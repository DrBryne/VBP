# VBP - Veiledende Behandlingsplan (Clinical Synthesis Engine)

An automated clinical synthesis engine designed to process large volumes of nursing literature and generate condensed, evidence-based nursing plans. It bridges the gap between academic research and bedside practice by translating raw literature into standardized ICNP terminology. Built using the Google ADK (Agent Development Kit).

## Project Structure

```
vbp/
├── app/                        # Core agent code
│   ├── agent.py                # Main orchestrator logic and routers
│   ├── agent_engine_app.py     # Vertex AI Agent Engine deployment configuration
│   ├── agents/                 # Specialized sub-agents
│   │   ├── clinical_auditor/   # Evaluates findings for clinical safety and specificity
│   │   ├── clinical_extractor/ # Extracts findings from raw PDFs/XMLs
│   │   ├── clinical_taxonomist/# Maps natural language to ICNP standard terms
│   │   └── report_chat/        # Conversational agent for querying synthesis results
│   ├── app_utils/              # Deployment and telemetry utilities
│   ├── report_generator/       # Automated HTML dashboard generation
│   └── shared/                 # Shared models, taxonomy logic, and GCS tools
├── docs/                       # Project specifications and documentation
├── frontend/                   # Streamlit Chat UI for the ReportChatAgent
├── local_data/                 # Local outputs and artifacts (ignored in git)
├── scripts/                    # Utility and analysis scripts
│   ├── analysis/               # Scripts for analyzing synthesis runs and taxonomies
│   └── utils/                  # Diagnostic and test scripts
├── tests/                      # Unit and integration tests
├── Makefile                    # Build, test, and deployment commands
└── pyproject.toml              # Python project configuration
```

## Requirements

- **uv**: Python package manager - [Install](https://docs.astral.sh/uv/getting-started/installation/)
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)
- **make**: Build automation tool

## Setup & Quick Start

1. Install dependencies:
```bash
make install
```

2. Run tests to ensure everything is working:
```bash
uv run pytest
```

3. Launch the development playground (for backend agent testing):
```bash
make playground
```

4. Launch the Conversational UI (Streamlit):
```bash
make run-ui
```

## Deployment

Deploying the backend to Vertex AI Agent Engine:
```bash
make deploy
```

Deploying the frontend UI to Google Cloud Run:
```bash
make deploy-ui
```

## Documentation
For deeper architectural details, read the [Design Specification](docs/DESIGN_SPEC.md).
