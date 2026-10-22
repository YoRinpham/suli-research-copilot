"""Pure deterministic report exporters. No provider calls or file writes."""

from html import escape
import json
import re


SECTIONS = (
    ("executive_summary", "Executive Summary"),
    ("research_question", "Research Question"),
    ("evidence_landscape", "Evidence Landscape"),
    ("gap", "Corpus-scoped Research Gap"),
    ("hypothesis", "Falsifiable Hypothesis"),
    ("plan", "Experiment Plan"),
    ("dataset", "Dataset and Data Preparation"),
    ("analysis", "Analysis Method"),
    ("results", "Results"),
    ("figures", "Visualization"),
    ("interpretation", "Interpretation"),
    ("limitations", "Limitations"),
    ("conclusion", "Conclusion"),
    ("citations", "References / Provenance"),
)


def report_sections(report: dict) -> list[tuple[str, str, list[str]]]:
    bundle = report["source_bundle"]
    blocks = report["narrative_blocks"]
    gap = bundle.get("gap") or {}
    hypothesis = bundle.get("hypothesis") or {}
    interpretation = bundle.get("interpretation") or {}
    landscape = bundle.get("landscape") or {}
    source_map = bundle.get("source_map") or {}
    def source_label(token: str) -> str:
        source = source_map.get(token)
        return f"{token} ({source['type']} {source['id']})" if source else token
    prose = {key: [f"{block['text']} [{', '.join(source_label(token) for token in block['source_refs'])}]" for block in blocks
                   if block["section"] == key] for key, _ in SECTIONS}
    content = {
        "research_question": [bundle.get("research_question", "Unavailable in current project record.")],
        "evidence_landscape": [landscape["summary"]] if landscape.get("summary") else [],
        "gap": [f"Within the supplied literature: {gap.get('title', '')} — {gap.get('description', '')}"] if gap else [],
        "hypothesis": [hypothesis.get("statement", ""),
                       f"Falsification: {hypothesis.get('falsification_condition', '')}"] if hypothesis else [],
        "plan": [json.dumps(bundle["plan"], ensure_ascii=False, sort_keys=True)] if bundle.get("plan") else [],
        "dataset": [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in bundle.get("datasets", [])],
        "analysis": [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in bundle.get("analyses", [])],
        "results": [f"StatisticalResult {item['id']}: {json.dumps(item['values'], ensure_ascii=False, sort_keys=True)}"
                    for item in bundle.get("results", [])],
        "figures": [f"FigureArtifact {item['id']}: {item['title']} (SHA-256: {item['sha256']})"
                    for item in bundle.get("figures", [])],
        "interpretation": [interpretation.get("summary", ""),
                           interpretation.get("hypothesis_relationship", ""),
                           interpretation.get("uncertainty", "")] if interpretation else [],
        "limitations": [*interpretation.get("limitations", []),
                        *(bundle.get("plan") or {}).get("limitations", [])],
        "citations": [f"{item['token']}: {item['paper_title'] or item['source_filename']}, physical PDF page {item['page_number']}; Evidence {item['evidence_id']}: {item['source_text']}"
                      for item in bundle.get("citations", [])],
    }
    return [(key, heading, prose.get(key, []) + (content.get(key) or ["Unavailable in current project record."]))
            for key, heading in SECTIONS]


def render_markdown(report: dict, *, resource_base_url: str = "") -> str:
    label = "HUMAN ACCEPTED" if report["review_status"] == "accepted" else "DRAFT / NOT HUMAN ACCEPTED"
    if report.get("current_source_bundle") is False:
        label = "HISTORICAL / SOURCE SUPERSEDED — " + label
    def safe(value: str) -> str:
        return re.sub(r"([\\`*_{}\[\]()#+!<>|])", r"\\\1", value.replace("\n", " "))
    lines = [f"# {safe(report['title'])}", "", f"Status: {label}",
             f"Source bundle SHA-256: {report.get('source_bundle_hash', 'unavailable')}", ""]
    for key, heading, paragraphs in report_sections(report):
        lines.extend([f"## {heading}", ""])
        if key == "results" and report["source_bundle"].get("results"):
            lines.extend(["| Result ID | Metric | Value |", "| --- | --- | --- |"])
            for result in report["source_bundle"]["results"]:
                for metric, value in sorted(result["values"].items()):
                    lines.append(f"| {safe(result['id'])} | {safe(metric)} | {safe(json.dumps(value, ensure_ascii=False, sort_keys=True))} |")
            lines.append("")
        else:
            lines.extend([safe(p) + "\n" for p in paragraphs])
    project_id = report["source_bundle"].get("project_id")
    resource_base_url = resource_base_url.rstrip("/")
    if project_id:
        for figure in report["source_bundle"].get("figures", []):
            lines.append(f"![{safe(figure['title'])}]({resource_base_url}/api/v1/projects/{project_id}/figures/{figure['id']}/file)")
        for citation in report["source_bundle"].get("citations", []):
            lines.append(f"[{citation['token']} PDF page {citation['page_number']}]({resource_base_url}/api/v1/projects/{project_id}/papers/{citation['paper_id']}/file#page={citation['page_number']})")
    return "\n".join(lines)


def render_html(report: dict, *, resource_base_url: str = "") -> str:
    label = "HUMAN ACCEPTED" if report["review_status"] == "accepted" else "DRAFT / NOT HUMAN ACCEPTED"
    if report.get("current_source_bundle") is False:
        label = "HISTORICAL / SOURCE SUPERSEDED — " + label
    body = [f"<h1>{escape(report['title'])}</h1>", f"<strong>{escape(label)}</strong>"]
    for key, heading, paragraphs in report_sections(report):
        body.append(f"<section><h2>{escape(heading)}</h2>")
        if key == "results" and report["source_bundle"].get("results"):
            body.append("<table><thead><tr><th>Result ID</th><th>Metric</th><th>Value</th></tr></thead><tbody>")
            for result in report["source_bundle"]["results"]:
                for metric, value in sorted(result["values"].items()):
                    body.append(f"<tr><td>{escape(result['id'])}</td><td>{escape(metric)}</td><td>{escape(json.dumps(value, ensure_ascii=False, sort_keys=True))}</td></tr>")
            body.append("</tbody></table>")
        else:
            body.extend(f"<p>{escape(p)}</p>" for p in paragraphs)
        body.append("</section>")
    project_id = report["source_bundle"].get("project_id")
    resource_base_url = resource_base_url.rstrip("/")
    if project_id:
        for figure in report["source_bundle"].get("figures", []):
            url = f"{resource_base_url}/api/v1/projects/{project_id}/figures/{figure['id']}/file"
            body.append(f"<figure><img src='{escape(url, quote=True)}' alt='{escape(figure['title'], quote=True)}'><figcaption>{escape(figure['title'])}</figcaption></figure>")
        for citation in report["source_bundle"].get("citations", []):
            url = f"{resource_base_url}/api/v1/projects/{project_id}/papers/{citation['paper_id']}/file#page={citation['page_number']}"
            body.append(f"<p><a href='{escape(url, quote=True)}'>{escape(citation['token'])} physical PDF page {citation['page_number']}</a></p>")
    style = "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#17202a}section{break-inside:avoid;margin:2rem 0}img{max-width:100%;height:auto}strong{color:#8a3100}pre{white-space:pre-wrap}@media print{a{color:inherit}}</style>"
    return "<!doctype html><html><head><meta charset='utf-8'><title>Research Report</title>" + style + "</head><body>" + "".join(body) + "</body></html>"
