import asyncio
from typing import Any

import aiohttp

from app.shared.logging import VBPLogger

logger = VBPLogger("fhir_client")

class FhirTerminologyClient:
    """
    Asynchronous client for interacting with a FHIR Terminology Server (e.g., Ontoserver).
    Provides methods to query SNOMED CT/ICNP hierarchies for deterministic semantic merging.
    """

    # Public sandbox server for testing (uses International SNOMED release)
    BASE_URL = "https://r4.ontoserver.csiro.au/fhir"
    SYSTEM = "http://snomed.info/sct"

    def __init__(self, timeout_seconds: int = 15):
        """
        Initializes the client with appropriate headers and timeouts.
        In a production Norwegian environment (NHN), Bearer tokens would be added here.
        """
        self.headers = {
            "Accept": "application/json",
            # "Authorization": f"Bearer {nhn_token}" # Placeholder for future NHN HelseID integration
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        # Prevent hammering the public FHIR API when checking hundreds of concepts
        self._semaphore = asyncio.Semaphore(10)

    async def check_subsumption(self, code_a: str, code_b: str) -> str:
        """
        Checks the hierarchical relationship between two SNOMED CT concept IDs.
        Args:
            code_a: The first SNOMED SCTID (e.g., '129839007' for Risk for fall).
            code_b: The second SNOMED SCTID.
        Returns:
            One of: 
            - 'subsumed-by' (A is a child of B)
            - 'subsumes' (A is a parent of B)
            - 'equivalent' (A and B are the same concept)
            - 'not-subsumed' (No direct hierarchical link, or error occurred)
        """
        if not code_a or not code_b:
            return "not-subsumed"

        url = f"{self.BASE_URL}/CodeSystem/$subsumes"
        params = {
            "system": self.SYSTEM,
            "codeA": code_a,
            "codeB": code_b
        }

        async with self._semaphore:
            for attempt in range(5):
                try:
                    async with aiohttp.ClientSession(headers=self.headers, timeout=self.timeout) as session:
                        async with session.get(url, params=params) as response:
                            if response.status == 200:
                                data = await response.json()
                                # FHIR Parameters resource returns a list of 'parameter' objects
                                for param in data.get("parameter", []):
                                    if param.get("name") == "outcome":
                                        return param.get("valueCode", "not-subsumed")
                                return "not-subsumed"
                            elif response.status == 429:
                                logger.warning(f"FHIR Subsumption API Error: HTTP 429 (Attempt {attempt+1}/5).", code_a=code_a, code_b=code_b)
                                await asyncio.sleep(1.5 ** attempt) # Exponential backoff
                                continue
                            else:
                                logger.warning(f"FHIR Subsumption API Error: HTTP {response.status}", code_a=code_a, code_b=code_b)
                                return "not-subsumed"

                except asyncio.TimeoutError:
                    logger.warning("FHIR Subsumption Timeout", code_a=code_a, code_b=code_b)
                except Exception as e:
                    logger.error(f"FHIR Subsumption Connection Error: {e}", code_a=code_a, code_b=code_b)
                
                # Small wait before retrying on non-429 connection errors/timeouts
                await asyncio.sleep(1)

        # Safe fallback: if anything fails, assume they are distinct concepts
        return "not-subsumed"

    async def lookup_concept(self, code: str) -> dict[str, Any] | None:
        """
        Retrieves detailed information about a specific SNOMED CT concept, 
        including its parent concepts.
        Args:
            code: The SNOMED SCTID to lookup.
        Returns:
            A dictionary containing the 'display' term and a list of 'parent_ids',
            or None if the lookup fails.
        """
        if not code:
            return None

        url = f"{self.BASE_URL}/CodeSystem/$lookup"
        params = {
            "system": self.SYSTEM,
            "code": code,
            "property": "parent" # Specifically request parent IDs
        }

        async with self._semaphore:
            for attempt in range(5):
                try:
                    async with aiohttp.ClientSession(headers=self.headers, timeout=self.timeout) as session:
                        async with session.get(url, params=params) as response:
                            if response.status == 200:
                                data = await response.json()

                                result = {
                                    "display": "Unknown",
                                    "parent_ids": []
                                }

                                # Parse the FHIR Parameters resource
                                for param in data.get("parameter", []):
                                    if param.get("name") == "display":
                                        result["display"] = param.get("valueString", "Unknown")
                                    elif param.get("name") == "property":
                                        # Properties are nested
                                        prop_parts = param.get("part", [])
                                        is_parent = any(p.get("name") == "code" and p.get("valueCode") == "parent" for p in prop_parts)
                                        if is_parent:
                                            for p in prop_parts:
                                                if p.get("name") == "value":
                                                    result["parent_ids"].append(p.get("valueCode"))

                                return result
                            elif response.status == 429:
                                logger.warning(f"FHIR Lookup API Error: HTTP 429 (Attempt {attempt+1}/5).", code=code)
                                await asyncio.sleep(1.5 ** attempt) # Exponential backoff
                                continue
                            else:
                                logger.warning(f"FHIR Lookup API Error: HTTP {response.status}", code=code)
                                return None

                except asyncio.TimeoutError:
                    logger.warning("FHIR Lookup Timeout", code=code)
                except Exception as e:
                    logger.error(f"FHIR Lookup Connection Error: {e}", code=code)
                
                # Small wait before retrying on non-429 connection errors/timeouts
                await asyncio.sleep(1)

        return None
