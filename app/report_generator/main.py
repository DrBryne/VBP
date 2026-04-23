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

def upload_to_gcs(content: bytes | str, gcs_uri: str):
    """Uploads content to a GCS path with correct content type."""
    if not gcs_uri.startswith("gs://"):
        return

    parts = gcs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_name = parts[1] if len(parts) > 1 else ""

    content_type = "application/pdf" if gcs_uri.lower().endswith(".pdf") else "text/html"

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)

    blob.cache_control = "no-store, no-cache, must-revalidate, max-age=0"
    
    if isinstance(content, str):
        blob.upload_from_string(content, content_type=content_type)
    else:
        blob.upload_from_string(content, content_type=content_type)
        
    print(f"Report successfully uploaded to: {gcs_uri}")

def generate_report_from_data(synthesis: SynthesisResponse, output_path: str):
    """Generates an HTML or PDF report directly from a SynthesisResponse object."""
    is_pdf = output_path.lower().endswith(".pdf")
    template_name = "report_pdf.html" if is_pdf else "report.html"

    # Setup Jinja2 environment
    template_dir = Path(__file__).parent / "templates"
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_dir),
        autoescape=jinja2.select_autoescape(['html', 'xml'])
    )

    try:
        template = env.get_template(template_name)
    except Exception as e:
        print(f"Error loading template {template_name}: {e}")
        sys.exit(1)

    # Prepare the context
    context = synthesis.model_dump()

    # Inject embedded CSS
    try:
        css_path = os.path.join(template_dir, "compiled_tailwind.css")
        if os.path.exists(css_path):
            with open(css_path, "r", encoding="utf-8") as f:
                context["embedded_css"] = f.read()
        else:
            context["embedded_css"] = "/* Compiled CSS not found */"
    except Exception as e:
        print(f"Error loading compiled CSS: {e}")
        context["embedded_css"] = "/* Error loading CSS */"

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

    # DASHBOARD LOGIC (shared between HTML and PDF)
    fo_names = {
        1: "Kommunikasjon/sanser",
        2: "Kunnskap/utvikling/psykisk",
        3: "Respirasjon/sirkulasjon",
        4: "Ernæring/væske/elektrolyttbalanse",
        5: "Eliminasjon",
        6: "Hud/vev/sår",
        7: "Aktivitet/funksjonsstatus",
        8: "Smerte/søvn/hvile/velvære",
        9: "Seksualitet/reproduksjon",
        10: "Sosiale forhold/miljø",
        11: "Åndelig/kulturelt/livsavslutning",
        12: "Annet/legedelegerte aktiviteter"
    }
    
    fo_counts = {i: {'name': name, 'count': 0, 'density': 0} for i, name in fo_names.items()}
    total_findings = len(context.get('synthesized_findings', []))
    certainty_counts = {'Høy': 0, 'Moderat': 0, 'Lav': 0, 'Ukjent': 0}

    for finding in context.get('synthesized_findings', []):
        fo_str = str(finding.get('FO', ''))
        try:
            fo_num = int(fo_str.split('.')[0])
            if fo_num in fo_counts:
                fo_counts[fo_num]['count'] += 1
        except:
            pass
            
        cert = finding.get('certainty_level', 'Ukjent')
        if cert in ['Høy', 'høy', 'Hoy', 'hoy']: certainty_counts['Høy'] += 1
        elif cert in ['Moderat', 'moderat']: certainty_counts['Moderat'] += 1
        elif cert in ['Lav', 'lav']: certainty_counts['Lav'] += 1
        else: certainty_counts['Ukjent'] += 1

    max_fo_count = max([data['count'] for data in fo_counts.values()]) if total_findings > 0 else 1
    for i in fo_counts:
        fo_counts[i]['density'] = (fo_counts[i]['count'] / max_fo_count * 100) if max_fo_count > 0 else 0

    evidence_levels = {
        'Nivå 5': {'label': 'Systembaser', 'count': 0, 'p_width': '50%'},
        'Nivå 4': {'label': 'Kliniske oppslagsverk', 'count': 0, 'p_width': '60%'},
        'Nivå 3': {'label': 'Retningslinjer/prosedyrer', 'count': 0, 'p_width': '70%'},
        'Nivå 2': {'label': 'Systematiske oversikter', 'count': 0, 'p_width': '80%'},
        'Nivå 1': {'label': 'Enkeltstudier', 'count': 0, 'p_width': '90%'},
        'Nivå 0': {'label': 'Annet / Ugradert', 'count': 0, 'p_width': '100%'},
    }
    
    total_docs = len(context.get('source_documents', []))
    for doc in context.get('source_documents', []):
        level_str = doc.get('evidence_level', '')
        if 'Nivå 5' in level_str: evidence_levels['Nivå 5']['count'] += 1
        elif 'Nivå 4' in level_str: evidence_levels['Nivå 4']['count'] += 1
        elif 'Nivå 3' in level_str: evidence_levels['Nivå 3']['count'] += 1
        elif 'Nivå 2' in level_str: evidence_levels['Nivå 2']['count'] += 1
        elif 'Nivå 1' in level_str: evidence_levels['Nivå 1']['count'] += 1
        else: evidence_levels['Nivå 0']['count'] += 1
        
    max_ev = max([data['count'] for data in evidence_levels.values()]) if total_docs > 0 else 1
    for lvl in evidence_levels:
        evidence_levels[lvl]['pct'] = (evidence_levels[lvl]['count'] / total_docs * 100) if total_docs else 0
        evidence_levels[lvl]['opacity'] = 0.2 + (evidence_levels[lvl]['count'] / max_ev) * 0.8 if max_ev > 0 else 0

    graded_evidence_count = 0
    total_evidence_count = 0
    for finding in context.get('synthesized_findings', []):
        for evidence in finding.get('supporting_evidence', []):
            total_evidence_count += len(evidence.get('quotes', []))
            if evidence.get('evidence_grade') or evidence.get('recommendation_strength'):
                graded_evidence_count += len(evidence.get('quotes', []))
                
    graded_pct = (graded_evidence_count / total_evidence_count * 100) if total_evidence_count else 0
    
    def get_certainty_desc(level):
        level = str(level).lower()
        if 'høy' in level or 'hoy' in level:
            return "Funnene er direkte og entydig beskrevet i kildematerialet, med lite rom for tolkning."
        if 'moderat' in level:
            return "Funnene er underbygget av kildene, men kan kreve noe faglig tolkning eller kontekstualisering."
        return "Funnene er mer indirekte utledet fra kildene, og bør vurderes nøye opp mot øvrig klinisk erfaring."

    def get_specificity_desc(score):
        try:
            score = float(score)
            if score >= 9: return "Innholdet er høyt spesialisert og skreddersydd for denne spesifikke pasientgruppen."
            if score >= 7: return "Innholdet er i stor grad rettet mot denne pasientgruppen, men inneholder også mer generelle elementer."
            if score >= 4: return "Innholdet er delvis relevant for målgruppen, men er i hovedsak av generell klinisk natur."
            return "Innholdet er av svært generell karakter og gjelder for de fleste pasientkategorier."
        except:
            return "Spesifisitet er ikke vurdert."

    def get_actionability_desc(score):
        try:
            score = float(score)
            if score >= 9: return "Tiltakene er svært konkrete og kan iverksettes direkte uten behov for omfattende lokal tilpasning."
            if score >= 7: return "Tiltakene er tydelig beskrevet og gir gode føringer for praktisk gjennomføring."
            if score >= 4: return "Tiltakene gir en overordnet retning, men krever lokal vurdering og konkretisering før iverksettelse."
            return "Tiltakene er på et prinsipielt nivå og krever betydelig faglig tolkning og planlegging."
        except:
            return "Tiltaksmulighet er ikke vurdert."

    for finding in context.get('synthesized_findings', []):
        finding['certainty_desc'] = get_certainty_desc(finding.get('certainty_level'))
        finding['specificity_desc'] = get_specificity_desc(finding.get('avg_specificity', 0))
        finding['actionability_desc'] = get_actionability_desc(finding.get('avg_actionability', 0))

    context['dashboard'] = {
        'evidence_density': fo_counts,
        'knowledge_base': evidence_levels,
        'graded_evidence': {
            'count': graded_evidence_count,
            'total': total_evidence_count,
            'pct': round(graded_pct, 1)
        },
        'certainty': {
            'high': certainty_counts['Høy'],
            'med': certainty_counts['Moderat'],
            'low': certainty_counts['Lav'] + certainty_counts['Ukjent'],
            'high_pct': round((certainty_counts['Høy'] / total_findings * 100) if total_findings else 0, 1),
            'med_pct': round((certainty_counts['Moderat'] / total_findings * 100) if total_findings else 0, 1),
            'low_pct': round(((certainty_counts['Lav'] + certainty_counts['Ukjent']) / total_findings * 100) if total_findings else 0, 1)
        }
    }

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
        final_html = template.render(**context)

        if is_pdf:
            import weasyprint
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            weasyprint.HTML(string=final_html, base_url=str(template_dir)).write_pdf(output_path)
            print(f"PDF-rapport generert ved: {output_path}")
        else:
            if output_path.startswith("gs://"):
                upload_to_gcs(final_html, output_path)
            else:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(final_html)
                print(f"HTML-rapport generert ved: {output_path}")
    except Exception as e:
        print(f"Error generating report: {e}")
        import traceback
        traceback.print_exc()
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
