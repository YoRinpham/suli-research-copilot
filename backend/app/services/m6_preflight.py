"""Sanitized, bounded demo-readiness diagnostics."""

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
import os
import json
import socket
import time
import httpx

from sqlalchemy import create_engine, text

from app.config.settings import Settings
from app.providers.llm import DashScopeLLMProvider, LLMFormatError, LLMProviderError
from pydantic import BaseModel, Field


M6_HEAD = "0010_m6_report_delivery"
M6_SCENARIO = Path(__file__).resolve().parents[2] / "evaluation" / "m6_scenario.json"


def proxy_facts(provider_host: str, environment: Mapping[str, str]) -> dict[str, bool]:
    no_proxy = environment.get("NO_PROXY", environment.get("no_proxy", ""))
    rules = [rule.strip().lower().lstrip(".") for rule in no_proxy.split(",") if rule.strip()]
    host = provider_host.lower()
    return {"https_proxy_configured": bool(environment.get("HTTPS_PROXY") or environment.get("https_proxy")),
            "all_proxy_configured": bool(environment.get("ALL_PROXY") or environment.get("all_proxy")),
            "no_proxy_configured": bool(no_proxy),
            "no_proxy_matches_provider": any(host == rule or host.endswith("." + rule) for rule in rules)}


def classify_preflight(checks: Mapping[str, bool | None]) -> str:
    if any(checks.get(name) is not True for name in ("database", "migration", "pgvector", "storage")):
        return "BLOCKED"
    if checks.get("frontend_http") is False or checks.get("backend_api") is False:
        return "BLOCKED"
    if checks.get("provider") is not True:
        return "DEGRADED"
    return "READY"


class TinyResponse(BaseModel):
    answer: str = Field(min_length=1)


def run_preflight(settings: Settings, *, provider_smoke: bool = False,
                  network_check: bool = False, http_check: bool = False,
                  demo_assets: list[Path] | None = None) -> dict:
    host = urlparse(settings.qwen_base_url).hostname or ""
    checks: dict[str, bool | None] = {"configuration": True, "database": False,
        "migration": False, "pgvector": False, "storage": False, "provider": None,
        "dns": None, "tcp": None, "demo_assets": True,
        "frontend_http": None, "backend_api": None}
    categories: list[str] = []
    try:
        with create_engine(settings.database_url, connect_args={"connect_timeout": 3}).connect() as connection:
            connection.execute(text("SELECT 1"))
            checks["database"] = True
            checks["migration"] = connection.scalar(text("SELECT version_num FROM alembic_version")) == M6_HEAD
            checks["pgvector"] = bool(connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")))
    except Exception:
        categories.append("database_unavailable")
    try:
        for root in (settings.storage_root, settings.m5_storage_root):
            with NamedTemporaryFile(dir=root, prefix="m6-preflight-", delete=True) as file:
                file.write(b"ok")
                file.flush()
        checks["storage"] = True
    except OSError:
        categories.append("storage_unwritable")
    if demo_assets is not None:
        for asset in demo_assets:
            if not asset.is_file():
                checks["demo_assets"] = False
                categories.append("demo_asset_missing")
    else:
        try:
            scenario = json.loads(M6_SCENARIO.read_text(encoding="utf-8"))
            for asset in [*scenario["corpus_manifest"], scenario["dataset_manifest"]]:
                path = (M6_SCENARIO.parent / asset["path"]).resolve()
                if (not path.is_relative_to(M6_SCENARIO.parent.resolve()) or not path.is_file() or
                    sha256(path.read_bytes()).hexdigest() != asset["sha256"]):
                    checks["demo_assets"] = False
                    categories.append("demo_asset_missing_or_changed")
        except (OSError, ValueError, KeyError, TypeError):
            checks["demo_assets"] = False
            categories.append("demo_manifest_invalid")
    if network_check or provider_smoke:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            checks["dns"] = True
        except OSError:
            checks["dns"] = False
            categories.append("provider_dns_failed")
        if checks["dns"]:
            try:
                with socket.create_connection((host, 443), timeout=3):
                    checks["tcp"] = True
            except OSError:
                checks["tcp"] = False
                categories.append("provider_tcp_failed")
    configured = bool(settings.qwen_api_key and settings.qwen_api_key.get_secret_value())
    checks["provider"] = False if not configured or checks["dns"] is False or checks["tcp"] is False else None
    if not configured:
        categories.append("provider_key_missing")
    smoke: dict | None = None
    if provider_smoke and configured and checks["dns"] is True and checks["tcp"] is True:
        started = time.monotonic()
        try:
            provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), settings.qwen_model,
                min(settings.provider_timeout_seconds, 20), 0, base_url=settings.qwen_base_url,
                reasoning_effort=settings.qwen_reasoning_effort)
            provider.generate_structured("Return the JSON answer 'ok'.", TinyResponse, {"demo": "synthetic"})
            checks["provider"] = True
            smoke = {"success": True, "latency_seconds": round(time.monotonic() - started, 3),
                     "http_response_received": True, "failure_category": None}
        except LLMProviderError as exc:
            checks["provider"] = False
            smoke = {"success": False, "latency_seconds": round(time.monotonic() - started, 3),
                     "http_response_received": exc.http_status is not None or isinstance(exc, LLMFormatError)
                     or exc.category == "provider_model_mismatch",
                     "failure_category": exc.category}
    if checks["demo_assets"] is False:
        checks["storage"] = False
    if http_check:
        for name, url in (("frontend_http", "http://127.0.0.1:3000/"),
                          ("backend_api", "http://127.0.0.1:8000/api/v1/health")):
            try:
                with httpx.Client(trust_env=False, timeout=3) as client:
                    checks[name] = client.get(url).status_code == 200
            except httpx.RequestError:
                checks[name] = False
            if checks[name] is False:
                categories.append(f"{name}_unavailable")
    return {"status": classify_preflight(checks), "checks": checks,
            "provider_model": settings.qwen_model, "embedding_model": settings.embedding_model,
            "provider_host": host, "provider_key_configured": configured,
            "proxy": proxy_facts(host, os.environ), "failure_categories": categories,
            "provider_smoke": smoke}
