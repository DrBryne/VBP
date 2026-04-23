import json

def analyze_missing_terms():
    with open("workflow_synthesis.json", "r") as f:
        synthesis = json.load(f)
    with open("taxonomy_cache.json", "r") as f:
        cache = json.load(f)

    cache_ids = set(cache.get("concepts", {}).keys())
    missing_map = {}

    def check_term(term_obj):
        cid = term_obj.get("ICNP_concept_id")
        term_text = term_obj.get("term")
        if cid and cid.isdigit() and cid not in cache_ids:
            if cid not in missing_map:
                missing_map[cid] = {"term": term_text, "count": 0}
            missing_map[cid]["count"] += 1

    for finding in synthesis.get("synthesized_findings", []):
        check_term(finding.get("nursing_diagnosis", {}))
        for item in finding.get("interventions", []):
            check_term(item)
        for item in finding.get("goals", []):
            check_term(item)

    print(f"Total Unique IDs Missing from Cache: {len(missing_map)}")
    print("\nDetailed breakdown (ID | Term | Frequency):")
    print("-" * 50)
    for cid, data in sorted(missing_map.items(), key=lambda x: x[1]["count"], reverse=True):
        print(f"{cid} | {data['term']} | {data['count']}")

if __name__ == "__main__":
    analyze_missing_terms()
