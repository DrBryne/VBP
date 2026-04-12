import asyncio
import json
import os
import re
import aiohttp
from collections import defaultdict
from google.cloud import storage

# Configure
PROJECT_ID = "sunny-passage-362617"
BUCKET_NAME = "veiledende_behandlingsplan"
CACHE_BLOB_NAME = "cache/taxonomy_cache.json"
FHIR_BASE_URL = "https://ontoserver.csiro.au/fhir"
FHIR_SYSTEM = "http://projecticnp.org"

# Extract failed code pairs from the text file
failed_pairs = set()
with open("tests/integration/results/recent_fhir_errors.txt", "r") as f:
    for line in f:
        match = re.search(r"code_a=(\d+), code_b=(\d+)", line)
        if match:
            failed_pairs.add((match.group(1), match.group(2)))

print(f"Extracted {len(failed_pairs)} unique failed FHIR subsumption pairs.")

async def download_cache():
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)
    if blob.exists():
        data = blob.download_as_string()
        return json.loads(data)
    return {"subsumption": {}, "concept": {}}

async def upload_cache(cache_data):
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)
    blob.upload_from_string(json.dumps(cache_data, indent=2), content_type="application/json")
    print(f"Successfully uploaded updated cache with {len(cache_data['subsumption'])} subsumption entries.")

async def check_subsumption_with_retry(session, code_a, code_b):
    url = f"{FHIR_BASE_URL}/CodeSystem/$subsumes"
    params = {"system": FHIR_SYSTEM, "codeA": code_a, "codeB": code_b}
    
    for attempt in range(5):
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                for param in data.get("parameter", []):
                    if param.get("name") == "outcome":
                        return param.get("valueCode", "not-subsumed")
                return "not-subsumed"
            elif response.status == 429:
                await asyncio.sleep(2 * (attempt + 1)) # Backoff
            else:
                return "error"
    return "error"

async def main():
    cache_data = await download_cache()
    subsumption_cache = cache_data.setdefault("subsumption", {})
    
    async with aiohttp.ClientSession() as session:
        for code_a, code_b in failed_pairs:
            cache_key = f"{code_a}||{code_b}"
            if cache_key in subsumption_cache:
                continue
                
            print(f"Querying {code_a} vs {code_b}...")
            result = await check_subsumption_with_retry(session, code_a, code_b)
            if result != "error":
                subsumption_cache[cache_key] = result
            
            # Rate limit ourselves to avoid triggering 429s again
            await asyncio.sleep(0.5)

    await upload_cache(cache_data)

if __name__ == "__main__":
    asyncio.run(main())
