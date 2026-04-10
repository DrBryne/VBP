import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

import jinja2
import markdown
from app.shared.models import SynthesisResponse

def gcs_to_http(uri: str) -> str:
    """Converts a gs:// URI to a public storage.googleapis.com URL."""
    if not uri or not uri.startswith("gs://"):
        return uri
    return f"https://storage.googleapis.com/{uri[5:]}"

def generate_report(input_path: str, output_path: str):
    """Generates an HTML report from a JSON synthesis result."""
    
    # 1. Load and parse the JSON data
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Patch execution_summary to match Pydantic model if fields are missing/different
        # The user requested NOT to change the model, so we adapt the input data.
        summary = data.get("execution_summary", {})
        
        # Ensure required fields for Pydantic validation are present
        defaults = {
            "total_hallucinated_citations": summary.get("total_rectified_quotes", 0),
            "total_taxonomy_errors": summary.get("total_taxonomy_errors", 0),
            "quality_notes": summary.get("quality_notes", "Ingen kvalitetsvurdering tilgjengelig.")
        }
        
        for key, val in defaults.items():
            if key not in summary:
                summary[key] = val
        
        data["execution_summary"] = summary

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
    context = synthesis.model_dump()
    
    # Ensure Enums are converted to strings for the template
    for finding in context.get('synthesized_findings', []):
        if hasattr(finding.get('FO'), 'value'):
            finding['FO'] = finding['FO'].value
        else:
            finding['FO'] = str(finding.get('FO', ''))
    
    # Sort findings by FO (numeric sort)
    def get_fo_sort_key(finding):
        fo = finding.get('FO', '')
        try:
            return int(fo.split('.')[0])
        except (ValueError, IndexError):
            return 999
            
    context['synthesized_findings'].sort(key=get_fo_sort_key)
    
    # Convert GCS URIs to HTTP links
    for doc in context.get('source_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))
    
    for doc in context.get('excluded_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))

    # Convert overall quality notes (use patched summary)
    q_notes = summary.get("quality_notes", "Ingen kvalitetsvurdering tilgjengelig.")
    context['quality_notes_html'] = markdown.markdown(q_notes)
    
    # 4. Render and save
    try:
        html_output = template.render(**context)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_output)
            
        print(f"Report successfully generated at: {output_path}")
    except Exception as e:
        print(f"Error rendering template: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a clinical synthesis HTML report.")
    parser.add_argument("--input", required=True, help="Path to the input JSON file.")
    parser.add_argument("--output", default="report.html", help="Path to save the HTML report.")
    
    args = parser.parse_args()
    generate_report(args.input, args.output)
