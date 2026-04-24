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
CSV_PATH = "app/agents/clinical_taxonomist/data/SNOMED_ICNP.csv"

# Helsedirektoratet Snowstorm Browser API (better for hierarchy)
BROWSER_BASE_URL = "https://snowstorm.terminologi.helsedirektoratet.no/snowstorm/snomed-ct/browser/MAIN/SNOMEDCT-NO/2026-03-15/concepts"

async def fetch_concept_details(session, concept_id):
    """Fetches a single concept's Norwegian term and its parents."""
    url = f"{BROWSER_BASE_URL}/{concept_id}"
    headers = {
        "accept": "application/json",
        "accept-language": "no",
        "x-requested-with": "XMLHttpRequest"
    }
    cookies = {"licenseCookie": "true"}

    try:
        async with session.get(url, headers=headers, cookies=cookies, timeout=10) as response:
            if response.status == 200:
                item = await response.json()
                display = item.get("pt", {}).get("term") or item.get("fsn", {}).get("term", "Unknown")

                # Fetch parents via the dedicated sub-endpoint
                parents_url = f"{url}/parents"
                parent_ids = []
                async with session.get(parents_url, headers=headers, cookies=cookies, timeout=10) as p_resp:
                    if p_p_resp_status := p_resp.status == 200:
                        p_items = await p_resp.json()
                        parent_ids = [p.get("conceptId") for p in p_items if p.get("conceptId")]

                return {
                    "display": display,
                    "parent_ids": parent_ids,
                    "source": "Helsedirektoratet_Browser"
                }
            return None
    except Exception:
        return None

async def main():
    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV not found at {CSV_PATH}")
        return

    # 1. Load unique IDs from CSV
    all_ids = []
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("Concept Id")
            if cid and cid.isdigit():
                all_ids.append(cid)

    unique_ids = sorted(set(all_ids))
    print(f"Loaded {len(unique_ids)} unique IDs from CSV.")

    # 2. Process with concurrency control
    semaphore = asyncio.Semaphore(20)
    final_results = {}

    async def worker(session, cid):
        async with semaphore:
            res = await fetch_concept_details(session, cid)
            if res:
                final_results[cid] = res
                print(f" [OK] {cid}: {res['display']} (Parents: {len(res['parent_ids'])})")
            else:
                print(f" [FAIL] {cid}")

    async with aiohttp.ClientSession() as session:
        tasks = [worker(session, cid) for cid in unique_ids]
        await asyncio.gather(*tasks)

    # 3. Save back to GCS
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)

    cache_data = {"subsumption": {}, "concepts": {}}
    if blob.exists():
        cache_data = json.loads(blob.download_as_string())

    concept_cache = cache_data.setdefault("concepts", {})
    for cid, data in final_results.items():
        concept_cache[cid] = data

    blob.upload_from_string(json.dumps(cache_data, indent=2), content_type="application/json")
    print(f"Successfully updated GCS cache with hierarchy. Total concepts: {len(concept_cache)}")

if __name__ == "__main__":
    asyncio.run(main())
