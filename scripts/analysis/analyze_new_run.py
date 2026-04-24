import json


def analyze_synthesis():
    with open("new_synthesis.json") as f:
        data = json.load(f)

    summary = data.get("execution_summary", {})
    print("--- EXECUTION SUMMARY ---")
    print(f"Total Files Processed: {summary.get('processed_files_count')}")
    print(f"Successful Files: {summary.get('successful_files_count')}")
    print(f"Excluded Files: {summary.get('excluded_files_count')}")
    print(f"Total Synthesized Findings: {summary.get('total_synthesized_findings')}")
    print(f"Dropped Findings: {summary.get('total_dropped_findings', 0)}")

    findings = data.get("synthesized_findings", [])

    # Check FO Distribution
    fo_counts = {}
    for f in findings:
        fo = f.get("FO", "Unknown")
        fo_counts[fo] = fo_counts.get(fo, 0) + 1

    print("\n--- FO DISTRIBUTION ---")
    for fo, count in sorted(fo_counts.items()):
        print(f"{fo}: {count} findings")

    # Check specific terms
    lidelse_found = False
    komm_terms = []

    for f in findings:
        diag = f.get("nursing_diagnosis", {})
        term = diag.get("term", "").lower()
        if "lidelse" in term:
            lidelse_found = True
        if "kommunikasjonsforstyrring" in term or "kommunikasjonshinder" in term:
            komm_terms.append(diag)

    print("\n--- BLACKLIST CHECK ---")
    print(f"Was 'lidelse' completely blocked from primary diagnoses? {'YES' if not lidelse_found else 'NO'}")

    print("\n--- COMMUNICATION CHECK ---")
    for kt in komm_terms:
        print(f"Found: {kt.get('term')} (ID: {kt.get('ICNP_concept_id')})")

    # Check Audit Trail
    excluded = data.get("excluded_documents", [])
    if excluded:
        print("\n--- AUDIT TRAIL SAMPLE ---")
        print(f"Excluded '{excluded[0].get('title')}' with reason: {excluded[0].get('justification')}")

if __name__ == "__main__":
    analyze_synthesis()
