# Troubleshooting Guide

This project includes a set of diagnostic scripts to help identify issues with API connectivity, Google Cloud Storage access, and environment configuration.

## Diagnostic Scripts

The diagnostic scripts are located in `tests/diagnostic/`. You can run them using `uv run python`.

### 1. API Connectivity (`test_api.py`)
Verifies that the Gemini API is accessible and that your credentials and location are correctly configured.
```bash
uv run python tests/diagnostic/test_api.py
```

### 2. GCS Access (`test_gcs.py`)
Checks if you have the necessary permissions to read files from the Google Cloud Storage buckets used by the workflow.
```bash
uv run python tests/diagnostic/test_gcs.py
```

### 3. REST API Test (`test_rest.py`)
Performs a direct REST API call to Vertex AI to bypass the SDK and isolate issues related to the client library.
```bash
uv run python tests/diagnostic/test_rest.py
```

### 4. Session State (`test_state.py`)
Tests the local `InMemorySessionService` to ensure that session state is being preserved correctly during workflow execution.
```bash
uv run python tests/diagnostic/test_state.py
```

### 5. Synchronous API (`test_sync.py`)
A simple check of the synchronous GenAI client to ensure basic model generation is working.
```bash
uv run python tests/diagnostic/test_sync.py
```

## Common Issues

### 401 Unauthorized / 403 Forbidden
This usually indicates a problem with your Google Cloud credentials. Ensure you have run:
```bash
gcloud auth application-default login
```

### Model Not Found (404)
If you see a 404 error for a model like `gemini-3.1-pro-preview`, it is likely due to the `GOOGLE_CLOUD_LOCATION` being set incorrectly. Many preview models require `location="global"`.

### Artifact Registry Errors
If `uv` fails to resolve dependencies due to `401 Unauthorized` on `us-python.pkg.dev`, the project is configured to use the public PyPI registry by default. Ensure your `pyproject.toml` contains:
```toml
[[tool.uv.index]]
name = "pypi"
url = "https://pypi.org/simple"
default = true
```
