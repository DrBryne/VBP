"""
Processing module facade.
Maintains backward compatibility by importing from specialized sub-modules.
"""
import asyncio
from app.shared.document_loader import (
    load_and_prep_document,
    index_document_sentences,
    format_indexed_text,
    get_cache_dir,
    strip_xml_tags
)
from app.shared.parsing_utils import safe_parse_json
from app.shared.taxonomy_validator import validate_taxonomy
from app.shared.pipeline import process_document_pipeline

# Re-export the semaphore logic for the orchestrator
_TAXONOMY_SEMAPHORE = None

def get_taxonomy_semaphore():
    global _TAXONOMY_SEMAPHORE
    if _TAXONOMY_SEMAPHORE is None:
        _TAXONOMY_SEMAPHORE = asyncio.Semaphore(5)
    return _TAXONOMY_SEMAPHORE
