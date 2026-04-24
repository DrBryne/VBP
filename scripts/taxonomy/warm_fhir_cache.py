import asyncio
import csv
import json
import os

import aiohttp
from google.cloud import storage

# Configure
PROJECT_ID = "sunny-passage-362617"
BUCKET_NAME = "veiledende_behandlingsplan"
CACHE_BLOB_NAME = "cache/taxonomy_cache.json"
FHIR_BASE_URL = "https://r4.ontoserver.csiro.au/fhir"
FHIR_SYSTEM = "http://snomed.info/sct"

# Path to the local CSV containing the terminology
CSV_PATH = "app/agents/clinical_taxonomist/data/SNOMED_ICNP.csv"

async def download_cache():
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)
    if blob.exists():
        data = blob.download_as_string()
        return json.loads(data)
    return {"subsumption": {}, "concepts": {}}

async def upload_cache(cache_data):
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)
    blob.upload_from_string(json.dumps(cache_data, indent=2), content_type="application/json")
    print(f"Successfully uploaded updated cache with {len(cache_data.get('concepts', {}))} concept entries.")

async def lookup_concept_with_retry(session, code):
    """Fetches display name and hierarchy for a concept."""
    url = f"{FHIR_BASE_URL}/CodeSystem/$lookup"
    params = {"system": FHIR_SYSTEM, "code": code}

    for attempt in range(5):
        try:
            async with session.get(url, params=params, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    result = {"display": "Unknown", "parent_ids": []}
                    for param in data.get("parameter", []):
                        if param.get("name") == "display":
                            result["display"] = param.get("valueString")
                        if param.get("name") == "property":
                            sub_params = param.get("part", [])
                            is_parent_prop = False
                            for sub in sub_params:
                                if sub.get("name") == "code" and sub.get("valueCode") in ["parent", "subsumedBy"]:
                                    is_parent_prop = True
                                if is_parent_prop and sub.get("name") == "value":
                                    result["parent_ids"].append(sub.get("valueCode"))
                    return result
                elif response.status == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    return None
        except Exception:
            await asyncio.sleep(1)
    return None

async def main():
    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV not found at {CSV_PATH}")
        return

    # 1. Load all unique IDs from CSV
    all_ids = set()
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("Concept Id")
            if cid:
                all_ids.add(cid)

    print(f"Loaded {len(all_ids)} unique IDs from CSV.")

    # 2. Load existing cache
    cache_data = await download_cache()
    concept_cache = cache_data.setdefault("concepts", {})

    # 3. Filter for IDs not already cached
    missing_ids = [cid for cid in all_ids if cid not in concept_cache]
    print(f"{len(missing_ids)} IDs are missing from cache. Starting warming...")

    # 4. Warm up missing concepts (using a semaphore to avoid overloading the server)
    semaphore = asyncio.Semaphore(10)

    async def warm_one(session, cid):
        async with semaphore:
            res = await lookup_concept_with_retry(session, cid)
            if res:
                concept_cache[cid] = res
                print(f" [OK] {cid}: {res['display']}")
            else:
                print(f" [FAIL] {cid}")

    async with aiohttp.ClientSession() as session:
        tasks = [warm_one(session, cid) for cid in missing_ids]
        await asyncio.gather(*tasks)

    # 5. Save back to GCS
    await upload_cache(cache_data)

if __name__ == "__main__":
    asyncio.run(main())
