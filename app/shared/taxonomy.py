import os
import re
from functools import lru_cache

# Define the standard Norwegian Functional Areas (FO)
VALID_FO_CATEGORIES = [
    "1. Kommunikasjon/sanser",
    "2. Kunnskap/utvikling/psykisk",
    "3. Respirasjon/sirkulasjon",
    "4. Ernæring/væske/elektrolyttbalanse",
    "5. Eliminasjon",
    "6. Hud/vev/sår",
    "7. Aktivitet/funksjonsstatus",
    "8. Smerte/søvn/hvile/velvære",
    "9. Seksualitet/reproduksjon",
    "10. Sosiale forhold/miljø",
    "11. Åndelig/kulturelt/livsavslutning",
    "12. Annet/legedelegerte aktiviteter"
]

@lru_cache(maxsize=1)
def load_valid_icnp_ids() -> set[str]:
    """Loads all valid ICNP Concept IDs from the specialized reference files."""
    valid_ids = set()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    data_dir = os.path.join(project_root, "app", "agents", "clinical_taxonomist", "data")

    # Regex to match the start of lines that contain IDs (digits followed by |)
    id_pattern = re.compile(r'^(\d+)\|')

    # Load from all three split files
    for filename in ["diagnoses.txt", "interventions.txt", "goals.txt"]:
        file_path = os.path.join(data_dir, filename)
        if os.path.exists(file_path):
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    match = id_pattern.match(line)
                    if match:
                        valid_ids.add(match.group(1))
        else:
            print(f"Warning: Terminology file not found: {file_path}")

    return valid_ids

@lru_cache(maxsize=1)
def load_norwegian_term_map() -> dict[str, str]:
    """Loads a mapping of SNOMED/ICNP IDs to Norwegian preferred terms."""
    term_map = {}
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    csv_path = os.path.join(project_root, "app", "agents", "clinical_taxonomist", "data", "SNOMED_ICNP.csv")
    
    if os.path.exists(csv_path):
        import csv
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # The CSV has 'Concept Id' and 'Preferred Term'
                cid = row.get("Concept Id")
                nor_term = row.get("Preferred Term")
                if cid and nor_term:
                    term_map[cid] = nor_term
    return term_map

def get_norwegian_term(concept_id: str, fallback: str) -> str:
    """Returns the Norwegian preferred term for an ID if found in local data."""
    if not concept_id:
        return fallback
    term_map = load_norwegian_term_map()
    return term_map.get(concept_id, fallback)

def is_valid_fo(fo_string: str) -> bool:
    """Checks if the FO string matches one of the 12 standard categories."""
    if not fo_string:
        return False
    # Check if it starts with a valid number followed by a dot
    for valid in VALID_FO_CATEGORIES:
        if fo_string.strip() == valid:
            return True
    return False

def get_default_fo() -> str:
    """Returns the default 'Other' category."""
    return "12. Annet/legedelegerte aktiviteter"
