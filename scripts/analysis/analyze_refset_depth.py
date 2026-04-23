import json
import asyncio
from app.shared.fhir_client import FhirTerminologyClient

def get_refset_ids():
    with open("app/shared/resources/icnp_norwegian.json", "r") as f:
        data = json.load(f)
        return [item["id"] for item in data.get("items", [])]

def load_cache():
    with open("taxonomy_cache.json", "r") as f:
        return json.load(f).get("concepts", {})

def get_depth(cid, cache, current_depth=0, visited=None):
    if visited is None: visited = set()
    if not cid or cid == "138875005" or cid in visited: return current_depth
    visited.add(cid)
    c_info = cache.get(cid)
    if not c_info or not c_info.get("parent_ids"): return current_depth
    return get_depth(c_info["parent_ids"][0], cache, current_depth + 1, visited)

async def analyze_refset_depth():
    refset_ids = get_refset_ids()
    cache = load_cache()
    
    depth_counts = {}
    shallow_terms = []
    
    for cid in refset_ids:
        depth = get_depth(cid, cache)
        depth_counts[depth] = depth_counts.get(depth, 0) + 1
        if depth < 4:
            term = cache.get(cid, {}).get("display", "Unknown")
            shallow_terms.append(f"{cid} | {term} (Depth {depth})")

    print(f"Total Refset Terms: {len(refset_ids)}")
    print("\nDepth Distribution:")
    for d in sorted(depth_counts.keys()):
        print(f" Depth {d}: {depth_counts[d]} terms")
    
    print("\nSample Shallow Terms (Depth < 4):")
    for t in shallow_terms[:20]:
        print(f" - {t}")

if __name__ == "__main__":
    asyncio.run(analyze_refset_depth())
