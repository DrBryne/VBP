import argparse
import json
import os
import sys
from pathlib import Path

import jinja2
import markdown
from google.cloud import storage

from app.shared.models import SynthesisResponse


def gcs_to_http(uri: str) -> str:
    """Converts a gs:// URI to a public storage.googleapis.com URL."""
    if not uri or not uri.startswith("gs://"):
        return uri
    return f"https://storage.googleapis.com/{uri[5:]}"

def upload_to_gcs(content: str, gcs_uri: str):
    """Uploads string content to a GCS path."""
    if not gcs_uri.startswith("gs://"):
        return

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(content, content_type="text/html")
    print(f"Report successfully uploaded to: {gcs_uri}")

def generate_report_from_data(synthesis: SynthesisResponse, output_path: str):
    """Generates an HTML report directly from a SynthesisResponse object."""
    # Setup Jinja2 environment
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

    # Prepare the context
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
            return int(str(fo).split('.')[0])
        except (ValueError, IndexError):
            return 999

    context['synthesized_findings'].sort(key=get_fo_sort_key)

    # Convert GCS URIs to HTTP links
    for doc in context.get('source_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))

    for doc in context.get('excluded_documents', []):
        doc['http_uri'] = gcs_to_http(doc.get('source_uri', ''))

    # Convert overall quality notes
    q_notes = context.get("execution_summary", {}).get("quality_notes", "Ingen kvalitetsvurdering tilgjengelig.")
    context['quality_notes_html'] = markdown.markdown(q_notes)

    # Render and save
    try:
        html_output = template.render(**context)

        if output_path.startswith("gs://"):
            upload_to_gcs(html_output, output_path)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_output)
            print(f"Report successfully generated at: {output_path}")
    except Exception as e:
        print(f"Error rendering template: {e}")
        sys.exit(1)

def generate_report(input_path: str, output_path: str):
    """Generates an HTML report from a JSON synthesis result."""
    try:
        with open(input_path, encoding='utf-8') as f:
            data = json.load(f)
        synthesis = SynthesisResponse.model_validate(data)
    except Exception as e:
        print(f"Error parsing input JSON: {e}")
        sys.exit(1)

    generate_report_from_data(synthesis, output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a clinical synthesis HTML report.")
    parser.add_argument("--input", required=True, help="Path to the input JSON file.")
    parser.add_argument("--output", default="report.html", help="Path to save the HTML report.")

    args = parser.parse_args()
    generate_report(args.input, args.output)
