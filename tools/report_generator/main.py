import argparse
import json
import os
import sys
from pathlib import Path

import jinja2
import markdown
from app.shared.models import SynthesisResponse

def gcs_to_http(uri: str) -> str:
    """Converts a gs:// URI to a public storage.googleapis.com URL."""
    if not uri or not uri.startswith("gs://"):
        return uri
    # path = uri[5:] # gs:// is 5 chars
    return f"https://storage.googleapis.com/{uri[5:]}"

def generate_report(input_path: str, output_path: str):
    """Generates an HTML report from a JSON synthesis result."""
    
    # 1. Load and parse the JSON data
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Validate with Pydantic
        synthesis = SynthesisResponse.model_validate(data)
    except Exception as e:
        print(f"Error parsing input JSON: {e}")
        sys.exit(1)

    # 2. Setup Jinja2 environment
    template_dir = Path(__file__).parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=jinja2.select_autoescape(['html', 'xml'])
    )
    
    try:
        template = env.get_template("report.html")
    except Exception as e:
        print(f"Error loading template: {e}")
        sys.exit(1)

    # 3. Prepare the context
    # Convert markdown strings to HTML for the template
    # We create a dictionary version of the model to add the HTML fields
    context = synthesis.model_dump()
    
    # Sort findings by FO (numeric sort by extracting the leading number)
    def get_fo_sort_key(finding):
        fo = finding.get('FO', '')
        try:
            # Extract the leading number (e.g., "3" from "3. Respirasjon")
            return int(fo.split('.')[0])
        except (ValueError, IndexError):
            return 999 # Fallback for unexpected formats
            
    context['synthesized_findings'].sort(key=get_fo_sort_key)
    
    # Convert GCS URIs to HTTP links for all documents
    for doc in context.get('source_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))
    
    for doc in context.get('excluded_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))

    # Convert overall quality notes
    context['quality_notes_html'] = markdown.markdown(synthesis.execution_summary.quality_notes)
    
    # Convert individual finding summaries removed as evidence_summary is no longer in the model

    # 4. Render and save
    try:
        html_output = template.render(**context)
        
        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_output)
            
        print(f"Report successfully generated at: {output_path}")
    except Exception as e:
        print(f"Error rendering template: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a clinical synthesis HTML report.")
    parser.add_argument("--input", required=True, help="Path to the input JSON file (SynthesisResponse schema).")
    parser.add_argument("--output", default="report.html", help="Path to save the generated HTML report.")
    
    args = parser.parse_args()
    
    generate_report(args.input, args.output)
