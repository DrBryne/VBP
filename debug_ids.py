import asyncio
import json
from app.shared.fhir_client import FhirTerminologyClient

async def check():
    client = FhirTerminologyClient()
    res = await client.lookup_concept("397640006")
    if res:
        print(f"DISPLAY: {res.get('display')}")
        print(f"PARENTS: {res.get('parent_ids')}")
        
        # Check depth
        async def get_depth(cid, d=0):
            if not cid or cid == "138875005": return d
            p_res = await client.lookup_concept(cid)
            if not p_res or not p_res.get("parent_ids"): return d
            return await get_depth(p_res["parent_ids"][0], d+1)
        
        depth = await get_depth("397640006")
        print(f"DEPTH: {depth}")

if __name__ == "__main__":
    asyncio.run(check())
