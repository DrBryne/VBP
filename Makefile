
# ==============================================================================
# Installation & Setup
# ==============================================================================

# Install dependencies using uv package manager
install:
	@command -v uv >/dev/null 2>&1 || { echo "uv is not installed. Installing uv..."; curl -LsSf https://astral.sh/uv/0.8.13/install.sh | sh; source $HOME/.local/bin/env; }
	uv sync

# ==============================================================================
# Playground Targets
# ==============================================================================

# Launch local dev playground
playground:
	@echo "==============================================================================="
	@echo "| 🚀 Starting your agent playground...                                        |"
	@echo "|                                                                             |"
	@echo "| 💡 Try asking: What's the weather in San Francisco?                         |"
	@echo "|                                                                             |"
	@echo "| 🔍 IMPORTANT: Select the 'app' folder to interact with your agent.          |"
	@echo "==============================================================================="
	uv run adk web . --port 8501 --reload_agents

# Launch the Streamlit Chat UI for Report Synthesis
run-ui:
	@echo "==============================================================================="
	@echo "| 🚀 Starting the VBP Clinical Synthesis Chat...                              |"
	@echo "==============================================================================="
	uv run streamlit run frontend/main.py

# ==============================================================================
# Backend Deployment Targets
# ==============================================================================

# Deploy the agent remotely
# Usage: 
#   make deploy [SERVICE_ACCOUNT=sa@project.iam.gserviceaccount.com] [AGENT_IDENTITY=true] [SECRETS="KEY=SECRET_ID,..."]
#
# AGENT_IDENTITY: Set to true only if you need per-agent IAM identity (Preview).
# SERVICE_ACCOUNT: Recommended for production/enterprise environments.
deploy: requirements
	uv run -m app.app_utils.deploy \
		--source-packages=./app \
		--entrypoint-module=app.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=app/app_utils/.requirements.txt \
		--set-env-vars="LOGS_BUCKET_NAME=veiledende_behandlingsplan,VBP_GCS_URI=gs://veiledende_behandlingsplan/ALS/,VBP_TARGET_GROUP=ALS - Amytrofisk lateral sklerose" \
		$(if $(SERVICE_ACCOUNT),--service-account="$(SERVICE_ACCOUNT)") \
		$(if $(filter true,$(AGENT_IDENTITY)),--agent-identity) \
		$(if $(filter command line,$(origin SECRETS)),--set-secrets="$(SECRETS)")

# Export only top-level dependencies to speed up remote builds by using pre-cached versions
requirements:
	@echo "📦 Generating strict requirements.txt..."
	uv export --format requirements-txt --no-dev --no-editable --no-hashes --no-emit-project > app/app_utils/.requirements.txt
	sed -i 's/^google-cloud-aiplatform==/google-cloud-aiplatform\[agent-engines\]==/g' app/app_utils/.requirements.txt

# Alias for 'make deploy' for backward compatibility
backend: deploy

# Deploy the Streamlit Chat UI to Cloud Run
deploy-ui:
	@echo "==============================================================================="
	@echo "| ☁️  Deploying Streamlit Chat UI to Cloud Run...                            |"
	@echo "| NOTE: Requires deployment_metadata.json from \`make deploy\`                  |"
	@echo "==============================================================================="
	@if [ ! -f deployment_metadata.json ]; then \
		echo "❌ Error: deployment_metadata.json not found. Run 'make deploy' first."; exit 1; \
	fi
	gcloud run deploy vbp-chat-ui \
		--source . \
		--region $(or $(REGION),us-central1) \
		--allow-unauthenticated \
		--port 8080 \
		--clear-base-image \
		--set-env-vars="STREAMLIT_SERVER_ADDRESS=0.0.0.0"
	@echo "==============================================================================="
	@echo "| ✅ Deployment complete.                                                     |"
	@echo "| 🔒 NEXT STEP: Configure Identity Platform (Firebase Auth) in Cloud Console! |"
	@echo "|    1. Go to Identity Platform and click 'Enable Identity Platform'.         |"
	@echo "|    2. Add the 'Email / Password' provider.                                  |"
	@echo "|    3. Copy the 'apiKey' from the Application Setup Details panel.           |"
	@echo "|    4. Run this command to update the Cloud Run service with your key:       |"
	@echo "|       gcloud run services update vbp-chat-ui --set-env-vars=\"FIREBASE_API_KEY=YOUR_KEY\""
	@echo "|    5. Add users in the 'Users' tab in Identity Platform to grant access.    |"
	@echo "==============================================================================="


# ==============================================================================
# Visualization & Reporting
# ==============================================================================

# Quickly re-generate the HTML report from a local or remote (GCS) result
# Usage: 
#   make report (uses latest local)
#   make report INPUT=tests/integration/results/run_XYZ/workflow_synthesis.json
#   make report INPUT=gs://bucket/runs/run_XYZ/workflow_synthesis.json
report:
	@uv sync --extra tools
	@if echo "$(INPUT)" | grep -q "^gs://"; then \
		echo "☁️ Downloading remote result: $(INPUT)"; \
		gsutil cp $(INPUT) /tmp/vbp_input.json || exit 1; \
		INPUT_PATH=/tmp/vbp_input.json; \
	else \
		LATEST_JSON=$$(ls -td tests/integration/results/run_*/workflow_synthesis.json 2>/dev/null | head -n 1); \
		INPUT_PATH=$${INPUT:-$$LATEST_JSON}; \
		if [ -z "$$INPUT_PATH" ]; then echo "❌ No local results found and no GCS path provided."; exit 1; fi; \
	fi; \
	DRAFT_URI=gs://veiledende_behandlingsplan/reports/draft_vbp_report.html; \
	echo "📊 Generating report from: $$INPUT_PATH"; \
	uv run python app/report_generator/main.py --input $$INPUT_PATH --output tests/integration/results/latest_report.html && \
	uv run python app/report_generator/main.py --input $$INPUT_PATH --output $$DRAFT_URI

	@echo "✅ Local: tests/integration/results/latest_report.html"
	@echo "✅ Cloud: https://storage.cloud.google.com/veiledende_behandlingsplan/reports/draft_vbp_report.html"

# Convenience target to generate report from the absolute latest run on Agent Engine
report-latest-cloud:
	@LATEST_RUN=$$(gsutil ls gs://veiledende_behandlingsplan/runs/ | tail -n 1); \
	if [ -z "$$LATEST_RUN" ]; then echo "❌ No remote runs found."; exit 1; fi; \
	$(MAKE) report INPUT=$${LATEST_RUN}workflow_synthesis.json

# ==============================================================================
# Testing & Code Quality
# ==============================================================================

# Run unit and integration tests
test:
	uv sync --dev
	uv run pytest tests/unit && uv run pytest tests/integration

# ==============================================================================
# Agent Evaluation
# ==============================================================================

# Run agent evaluation using ADK eval
# Usage: make eval [EVALSET=tests/eval/evalsets/basic.evalset.json] [EVAL_CONFIG=tests/eval/eval_config.json]
eval:
	@echo "==============================================================================="
	@echo "| Running Agent Evaluation                                                    |"
	@echo "==============================================================================="
	uv sync --dev --extra eval
	uv run adk eval ./app $${EVALSET:-tests/eval/evalsets/basic.evalset.json} \
		$(if $(EVAL_CONFIG),--config_file_path=$(EVAL_CONFIG),$(if $(wildcard tests/eval/eval_config.json),--config_file_path=tests/eval/eval_config.json,))

# Run evaluation with all evalsets
eval-all:
	@echo "==============================================================================="
	@echo "| Running All Evalsets                                                        |"
	@echo "==============================================================================="
	@for evalset in tests/eval/evalsets/*.evalset.json; do \
		echo ""; \
		echo "▶ Running: $$evalset"; \
		$(MAKE) eval EVALSET=$$evalset || exit 1; \
	done
	@echo ""
	@echo "✅ All evalsets completed"

# Run code quality checks (codespell, ruff, ty)
lint:
	uv sync --dev --extra lint
	uv run codespell
	uv run ruff check . --diff
	uv run ruff format . --check --diff
	uv run ty check .

# ==============================================================================
# Gemini Enterprise Integration
# ==============================================================================

# Register the deployed agent to Gemini Enterprise
# Usage: make register-gemini-enterprise (interactive - will prompt for required details)
# For non-interactive use, set env vars: ID or GEMINI_ENTERPRISE_APP_ID (full GE resource name)
# Optional env vars: GEMINI_DISPLAY_NAME, GEMINI_DESCRIPTION, GEMINI_TOOL_DESCRIPTION, AGENT_ENGINE_ID
register-gemini-enterprise:
	@uvx agent-starter-pack@0.41.0 register-gemini-enterprise