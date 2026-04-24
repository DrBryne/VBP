import json
import os

from google.cloud import storage

# Configure
PROJECT_ID = "sunny-passage-362617"
BUCKET_NAME = "veiledende_behandlingsplan"
CACHE_BLOB_NAME = "cache/taxonomy_cache.json"
LOCAL_DATA_PATH = "icnp_parents_full.json"

def main():
    if not os.path.exists(LOCAL_DATA_PATH):
        print(f"Error: Local data not found at {LOCAL_DATA_PATH}")
        return

    # 1. Load authoritative data from Helsedirektoratet export
    with open(LOCAL_DATA_PATH, encoding="utf-8") as f:
        export_data = json.load(f)

    new_entries = {}
    for item in export_data.get("items", []):
        cid = item.get("conceptId")
        if not cid: continue

        # Extract preferred term (Norwegian)
        display = "Unknown"
        if item.get("pt") and item.get("pt").get("term"):
            display = item.get("pt").get("term")
        elif item.get("fsn") and item.get("fsn").get("term"):
            display = item.get("fsn").get("term")

        # Extract parents
        parent_ids = []
        # Snowstorm often provides parents in a specific field if expanded
        if item.get("parents"):
            for p in item.get("parents"):
                pid = p.get("conceptId")
                if pid:
                    parent_ids.append(pid)

        new_entries[cid] = {
            "display": display,
            "parent_ids": parent_ids,
            "source": "Helsedirektoratet"
        }

    print(f"Extracted {len(new_entries)} authoritative entries from local export.")

    # 2. Load existing cache from GCS
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(CACHE_BLOB_NAME)

    cache_data = {"subsumption": {}, "concepts": {}}
    if blob.exists():
        cache_data = json.loads(blob.download_as_string())

    concept_cache = cache_data.setdefault("concepts", {})

    # 3. Merge: Authoritative data overwrites existing cache entries
    update_count = 0
    new_count = 0
    for cid, data in new_entries.items():
        if cid in concept_cache:
            update_count += 1
        else:
            new_count += 1
        concept_cache[cid] = data

    # 4. Upload back to GCS
    blob.upload_from_string(json.dumps(cache_data, indent=2), content_type="application/json")

    print("Successfully merged data into GCS cache.")
    print(f" - New entries added: {new_count}")
    print(f" - Existing entries updated: {update_count}")
    print(f" - Total cache size: {len(concept_cache)} concepts")

if __name__ == "__main__":
    main()
