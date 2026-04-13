import json

def analyze_for_blacklist():
    with open("app/shared/resources/icnp_norwegian.json", "r") as f:
        data = json.load(f)
    
    items = data.get("items", [])
    
    # Keywords that suggest a term is a generic 'container' rather than a bedside finding
    generic_triggers = [
        "lidelse", "forstyrring", "funn", "tilstand", "problem", "situasjon",
        "prosess", "handling", "aktivitet", "metode", "prosedyre", "måte",
        "faktor", "kategori", "område", "enhet"
    ]
    
    proposed_blacklist = []
    
    for item in items:
        term = item.get("pt", {}).get("term", "").lower()
        concept_id = item.get("id")
        
        # 1. Exact match for very generic words
        if term in generic_triggers:
            proposed_blacklist.append((concept_id, term, "Single word generic"))
            continue
            
        # 2. Check if the term consists ONLY of generic words or is very short
        words = term.split()
        if len(words) == 1 and term in generic_triggers:
             proposed_blacklist.append((concept_id, term, "Generic single word"))
        
        # 3. High-level abstract concepts often found in ICNP/VIPS
        abstract_concepts = [
            "psykisk helse", "fysisk tilstand", "pleiebehov", "behandlingsmåte",
            "sykepleieintervensjon", "sykepleiediagnose", "klinisk funn",
            "pasientstatus", "funksjon", "evne", "behov"
        ]
        if term in abstract_concepts:
            proposed_blacklist.append((concept_id, term, "Abstract category"))

    print(f"Total Refset Terms Analyzed: {len(items)}")
    print(f"Proposed for Blacklist: {len(proposed_blacklist)}\n")
    print("ID | Term | Reason")
    print("-" * 50)
    for cid, term, reason in sorted(proposed_blacklist, key=lambda x: x[1]):
        print(f"{cid} | {term} | {reason}")

if __name__ == "__main__":
    analyze_for_blacklist()
