import mimetypes
import os
import re

import fitz  # PyMuPDF
import nltk
from bs4 import BeautifulSoup
from google.cloud import storage

from app.app_utils.telemetry import track_telemetry_span
from app.shared.logging import VBPLogger
from app.shared.tools import parse_gcs_uri

logger = VBPLogger("document_loader")

# Download NLTK data
try:
    nltk.data.find('tokenizers/punkt_tab')
except LookupError:
    nltk.download('punkt_tab')

def index_document_sentences(text: str) -> dict[str, str]:
    """Splits document text into individual sentences and assigns unique IDs."""
    text = re.sub(r'\s+', ' ', text).strip()
    sentences = nltk.sent_tokenize(text)
    return {f"S{i+1}": sent for i, sent in enumerate(sentences)}

def format_indexed_text(indexed_sentences: dict[str, str]) -> str:
    """Reconstructs the document with visible sentence IDs for LLM consumption."""
    parts = []
    for sid, text in indexed_sentences.items():
        parts.append(f"[{sid}] {text}")
    return " ".join(parts)

def strip_xml_tags(text: str) -> str:
    """Extracts pure text from XML/HTML strings, replacing tags with spaces."""
    if not text:
        return ""
    try:
        soup = BeautifulSoup(text, "lxml-xml")
        return soup.get_text(separator=' ', strip=True)
    except Exception as e:
        logger.error(f"Error stripping XML tags: {e}")
        return text

@track_telemetry_span("Document: Load and Prep")
def load_and_prep_document(uri: str, project_id: str) -> tuple[str, str, str]:
    """Downloads and cleans document text based on its file format."""
    filename = uri.split("/")[-1]
    try:
        mime_type, _ = mimetypes.guess_type(uri)
        bucket_name, blob_name = parse_gcs_uri(uri)

        if os.environ.get("AGENT_ENGINE_ID"):
            storage_client = storage.Client()
        else:
            storage_client = storage.Client(project=project_id)

        blob = storage_client.bucket(bucket_name).blob(blob_name)
        if mime_type == "application/pdf":
            pdf_bytes = blob.download_as_bytes()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            file_text = "".join([page.get_text() for page in doc])
            doc.close()
        else:
            raw_bytes = blob.download_as_bytes()
            file_text = raw_bytes.decode('utf-8', errors='replace')
            if mime_type in ["text/xml", "application/xml"]:
                file_text = strip_xml_tags(file_text)

        return filename, mime_type, file_text
    except Exception as e:
        logger.error(f"CRITICAL FAILURE for {filename}: {e}")
        raise e

def get_cache_dir() -> str:
    """Determines the correct temporary directory for disk-backed caching."""
    if os.environ.get("AGENT_ENGINE_ID"):
        cache_dir = "/tmp/vbp_indexes"
    else:
        cache_dir = ".adk/cache/indexes"

    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir
