import json
with open("latest_full_synthesis.json", "r") as f:
    data = json.load(f)
for finding in data.get("synthesized_findings", []):
    diag = finding.get("nursing_diagnosis", {})
    term = diag.get("term", "")
    if "lidelse" in term.lower():
        print(f"DIAG: {term} | ID: {diag.get('ICNP_concept_id')}")
