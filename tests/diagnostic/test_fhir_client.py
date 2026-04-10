import pytest
from app.shared.fhir_client import FhirTerminologyClient

@pytest.fixture
def fhir_client():
    return FhirTerminologyClient(timeout_seconds=10)

@pytest.mark.asyncio
async def test_check_subsumption_equivalent(fhir_client):
    # Testing the same code should return 'equivalent'
    code = "129839007"  # Risk for fall
    result = await fhir_client.check_subsumption(code, code)
    assert result == "equivalent"

@pytest.mark.asyncio
async def test_check_subsumption_subsumed_by(fhir_client):
    # 'Risk for fall' (129839007) is a child of 'Clinical finding' (404684003)
    child_code = "129839007"
    parent_code = "404684003"
    result = await fhir_client.check_subsumption(child_code, parent_code)
    assert result == "subsumed-by"

@pytest.mark.asyncio
async def test_check_subsumption_subsumes(fhir_client):
    # 'Clinical finding' (404684003) is a parent of 'Risk for fall' (129839007)
    parent_code = "404684003"
    child_code = "129839007"
    result = await fhir_client.check_subsumption(parent_code, child_code)
    assert result == "subsumes"

@pytest.mark.asyncio
async def test_check_subsumption_not_subsumed(fhir_client):
    # 'Risk for fall' (129839007) is NOT related to 'Paracetamol' (387517004)
    code_a = "129839007"
    code_b = "387517004"
    result = await fhir_client.check_subsumption(code_a, code_b)
    assert result == "not-subsumed"

@pytest.mark.asyncio
async def test_lookup_concept(fhir_client):
    # Look up details for 'Risk for fall' (129839007)
    code = "129839007"
    result = await fhir_client.lookup_concept(code)
    
    assert result is not None
    assert "At risk of falls" in result.get("display", "")
    assert isinstance(result.get("parent_ids"), list)
    assert len(result.get("parent_ids")) > 0
    # One of the parents should be "At risk for injury" (129832001) or a more recent parent like (1255669001)
    assert any(parent in result.get("parent_ids") for parent in ["129832001", "1255669001"])

@pytest.mark.asyncio
async def test_client_handles_invalid_code_gracefully(fhir_client):
    # Look up a non-existent code
    code = "9999999999999999999"
    result = await fhir_client.lookup_concept(code)
    assert result is None
    
    # Subsumption with invalid code
    sub_result = await fhir_client.check_subsumption(code, "404684003")
    assert sub_result == "not-subsumed"
