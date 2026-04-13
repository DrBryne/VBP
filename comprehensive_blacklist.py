import json
import re

def comprehensive_blacklist_analysis():
    with open("app/shared/resources/icnp_norwegian.json", "r") as f:
        data = json.load(f)
    
    items = data.get("items", [])
    
    # Expanded list of 'Generic' indicators in Norwegian
    generic_words = {
        "lidelse", "forstyrring", "funn", "tilstand", "problem", "situasjon",
        "prosess", "handling", "aktivitet", "metode", "prosedyre", "måte",
        "faktor", "kategori", "område", "enhet", "behov", "evne", "funksjon",
        "status", "tegn", "symptom", "oppfatning", "kunnskap", "verdi"
    }
    
    candidates = []
    
    for item in items:
        term = item.get("pt", {}).get("term", "").lower().strip()
        concept_id = item.get("id")
        words = term.split()
        
        reason = None
        
        # 1. Single word generic terms (e.g., "lidelse", "behov")
        if len(words) == 1 and term in generic_words:
            reason = "Single-word abstraction"
            
        # 2. Very short terms that are likely categories
        elif len(term) < 5:
            reason = "Too short/undespecified"
            
        # 3. High-level 'Problem' containers
        elif term in ["sykepleiediagnose", "klinisk funn", "sykepleieintervensjon", "pasientstatus"]:
            reason = "Meta-category"
            
        # 4. Pattern: 'Problem med [X]' where X is also generic
        elif term.startswith("problem med ") and len(words) <= 3:
            target = words[-1]
            if target in generic_words:
                reason = "Generic problem container"

        # 5. Pattern: '[X]-funn' or '[X]-status'
        elif any(term.endswith(suffix) for suffix in ["funn", "status", "situasjon"]) and len(words) == 1:
             reason = "Category suffix"

        if reason:
            candidates.append((concept_id, term, reason))

    print(f"Total Terms: {len(items)}")
    print(f"Blacklist Candidates: {len(candidates)}\n")
    print("ID | Norwegian Term | Reason")
    print("-" * 60)
    for cid, term, r in sorted(candidates, key=lambda x: x[1]):
        print(f"{cid} | {term} | {r}")

if __name__ == "__main__":
    comprehensive_blacklist_analysis()
