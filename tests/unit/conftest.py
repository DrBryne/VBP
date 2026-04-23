import pytest
from app.shared.consolidation import taxonomy_cache, norwegian_refset_ids
import copy

@pytest.fixture(autouse=True)
def isolated_taxonomy():
    # Make absolutely sure globals are clear before AND after tests
    original_cache = copy.deepcopy(taxonomy_cache)
    original_refset = set(norwegian_refset_ids)
    
    taxonomy_cache["concepts"].clear()
    taxonomy_cache["subsumption"].clear()
    norwegian_refset_ids.clear()
    
    yield
    
    taxonomy_cache.update(original_cache)
    norwegian_refset_ids.clear()
    norwegian_refset_ids.update(original_refset)
