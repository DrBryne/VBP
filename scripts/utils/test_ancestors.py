import asyncio

from app.shared.fhir_client import FhirTerminologyClient


async def get_all_ancestors(client, code, depth=0, max_depth=5):
    """Recursively fetches all ancestors for a given code."""
    if depth >= max_depth:
        return {}

    res = await client.lookup_concept(code)
    if not res:
        return {}

    ancestors = {code: res.get("display", "Unknown")}
    tasks = []
    for p_id in res.get("parent_ids", []):
        tasks.append(get_all_ancestors(client, p_id, depth + 1, max_depth))

    results = await asyncio.gather(*tasks)
    for r in results:
        ancestors.update(r)

    return ancestors

async def trace_path_to_root(client, code, path=None):
    """Traces a single linear path to the root for visualization."""
    if path is None:
        path = []

    res = await client.lookup_concept(code)
    if not res:
        return path

    term = res.get("display", "Unknown")
    path.append(f"{term} ({code})")

    if res.get("parent_ids"):
        # Just follow the first parent for a simple trace
        return await trace_path_to_root(client, res.get("parent_ids")[0], path)
    return path

async def main():
    client = FhirTerminologyClient()

    id1 = "278919001" # kommunikasjonsforstyrring
    id2 = "706881002" # kommunikasjonshinder

    print(f"Tracing ancestry for:\n1. {id1}\n2. {id2}\n")

    anc1 = await get_all_ancestors(client, id1)
    anc2 = await get_all_ancestors(client, id2)

    common = set(anc1.keys()).intersection(set(anc2.keys()))
    # Remove generic root concepts
    blocked = {"138875005", "404684003", "71388002", "243796009", "272379006", "123037004", "410607006"}
    common = common - blocked

    print("Common Ancestors found:")
    for c_id in common:
        print(f" - {anc1[c_id]} ({c_id})")

    print("\nSample Path for ID 1:")
    path1 = await trace_path_to_root(client, id1)
    for i, step in enumerate(path1):
        print("  " * i + "-> " + step)

    print("\nSample Path for ID 2:")
    path2 = await trace_path_to_root(client, id2)
    for i, step in enumerate(path2):
        print("  " * i + "-> " + step)

if __name__ == "__main__":
    asyncio.run(main())
