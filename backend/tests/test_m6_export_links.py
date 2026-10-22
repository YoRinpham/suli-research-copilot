"""Downloaded exports keep their backend resource origin outside the API page."""

import re
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import urljoin

from fastapi.testclient import TestClient

from app.api import m6
from app.config.settings import get_settings
from app.main import create_app
from app.services.m6_render import render_html, render_markdown


def test_downloaded_report_links_resolve_to_backend_without_changing_sources():
    report = {"title": "Synthetic report", "review_status": "accepted", "narrative_blocks": [],
              "source_bundle": {"project_id": "p", "figures": [{"id": "f", "title": "Synthetic plot", "sha256": "hash"}],
                  "citations": [{"token": "EVIDENCE_1", "paper_id": "paper", "page_number": 1,
                      "paper_title": None, "source_filename": "synthetic.pdf", "evidence_id": "e", "source_text": "Source"}]}}
    before = deepcopy(report)
    origin = "http://127.0.0.1:58838/"
    markdown = render_markdown(report, resource_base_url=origin)
    html = render_html(report, resource_base_url=origin)
    expected = {origin + "api/v1/projects/p/figures/f/file", origin + "api/v1/projects/p/papers/paper/file#page=1"}
    markdown_links = re.findall(r"\]\(([^)]+)\)", markdown)
    html_links = re.findall(r"(?:src|href)='([^']+)'", html)
    for links in (markdown_links, html_links):
        assert set(links) == expected
        assert {urljoin("file:///C:/exports/report.html", link) for link in links} == expected
    assert report == before


def test_export_route_passes_request_backend_origin(monkeypatch):
    captured = {}

    def export(*args, **kwargs):
        captured.update(kwargs)
        return "report"

    monkeypatch.setattr(m6, "service", lambda request: SimpleNamespace(export_report=export))
    with TestClient(create_app(get_settings()), base_url="http://127.0.0.1:58838") as client:
        response = client.get("/api/v1/projects/p/reports/r/export?format=html")
    assert response.status_code == 200
    assert captured["resource_base_url"].rstrip("/") == "http://127.0.0.1:58838"
