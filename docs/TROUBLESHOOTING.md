# VBP Workflow: Troubleshooting Guide

## Common Issues & Solutions

### 1. 503 Service Unavailable (Cloud Run/Agent Engine)
*   **Cause**: This is almost always a **Network Timeout** or a **Response Size Limit** issue.
*   **Symptom**: The job runs for 15-30 minutes and then returns a 503 error without a final JSON.
*   **Solution**: 
    1. Check if **Link-Based Handover** is enabled. Large synthesis results (>5MB) cannot be streamed reliably over HTTP. 
    2. Reduce `max_concurrency`. High concurrency (30+) can overload the container's memory and cause the streaming buffer to "snap."
    3. Ensure the job isn't exceeding the hard 30-minute request deadline on Vertex AI.

### 2. Broken/Truncated JSON (`EOF while parsing`)
*   **Cause**: **Memory Pressure** in the Python container.
*   **Symptom**: Logs show `pydantic.ValidationError` or `Invalid JSON: EOF`.
*   **Solution**: 
    1. Ensure the **Global Singleton Cache** is used for terminology files.
    2. Check **Log Truncation**. Large prompts/responses written to Cloud Logging (256KB limit) add significant overhead.
    3. Lower concurrency to 10-15 to give the garbage collector more room.

### 3. Consolidation Phase Hangs
*   **Cause**: The **FHIR Terminology Server** (CSIRO) is slow or throttled.
*   **Symptom**: Document processing is "DONE," but the summary doesn't appear for 10+ minutes.
*   **Solution**: 
    1. Run the `tests/integration/warm_fhir_cache.py` script to populate the persistent GCS cache.
    2. Ensure the FHIR base URL is set to the stable `r4` endpoint: `https://r4.ontoserver.csiro.au/fhir`.
    3. Verify that the cache is being loaded correctly at the start of the orchestration.

### 4. Diagnosis Displayed in English
*   **Cause**: The FHIR server returned an English term and it wasn't overwritten by the local Norwegian map.
*   **Solution**: 
    1. Ensure `SNOMED_ICNP.csv` is present in the `ClinicalTaxonomist` data folder.
    2. Check the `get_norwegian_term()` logic in `app/shared/taxonomy.py`.

---

## Technical Monitoring
- **Cloud Trace**: Search for "Workflow: Orchestration" to see the waterfall of document execution.
- **Cloud Logging**: Filter for `resource.type="aiplatform.googleapis.com/ReasoningEngine"` to see real-time progress updates.
