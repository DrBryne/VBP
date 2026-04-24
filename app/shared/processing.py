"""
Processing module facade.
Maintains backward compatibility by importing from specialized sub-modules.
"""
import asyncio

# Re-export the semaphore logic for the orchestrator
_TAXONOMY_SEMAPHORE = None

def get_taxonomy_semaphore():
    global _TAXONOMY_SEMAPHORE
    if _TAXONOMY_SEMAPHORE is None:
        _TAXONOMY_SEMAPHORE = asyncio.Semaphore(5)
    return _TAXONOMY_SEMAPHORE
