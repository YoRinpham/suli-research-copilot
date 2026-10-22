from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError as SQLTimeoutError

from app.main import create_app
from app.services.projects import ProjectService


def test_application_starts() -> None:
    with TestClient(create_app()) as client:
        assert client.app.title == "溯理 SULI API"


def test_health_reports_database_success(monkeypatch) -> None:
    monkeypatch.setattr("app.api.health.check_database_connection", lambda: None)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}
    assert UUID(response.headers["X-Request-ID"])


def test_health_echoes_supplied_request_id(monkeypatch) -> None:
    monkeypatch.setattr("app.api.health.check_database_connection", lambda: None)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "audit-123"})
    assert response.headers["X-Request-ID"] == "audit-123"


@pytest.mark.parametrize("failure", [OperationalError, InterfaceError])
def test_health_reports_database_failure(monkeypatch, failure) -> None:
    def fail() -> None:
        raise failure("SELECT 1", {}, Exception("private database detail"))

    monkeypatch.setattr("app.api.health.check_database_connection", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "audit-db-123"})
    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "audit-db-123"
    assert response.json() == {
        "error": {
            "code": "database_failed",
            "message": "Database unavailable",
            "stage": "health",
            "request_id": "audit-db-123",
            "details": {},
        }
    }
    assert "private database detail" not in response.text


def test_health_database_failure_generates_request_id(monkeypatch) -> None:
    def fail() -> None:
        raise OperationalError("SELECT 1", {}, Exception("private database detail"))

    monkeypatch.setattr("app.api.health.check_database_connection", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health")
    request_id = response.headers["X-Request-ID"]
    assert UUID(request_id)
    assert response.json()["error"]["request_id"] == request_id


def test_health_pool_timeout_is_database_unavailable(monkeypatch) -> None:
    def fail() -> None:
        raise SQLTimeoutError("private pool details")

    monkeypatch.setattr("app.api.health.check_database_connection", fail)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "pool-timeout-123"})
    assert response.status_code == 503
    assert response.headers["X-Request-ID"] == "pool-timeout-123"
    assert response.json() == {"error": {
        "code": "database_failed",
        "message": "Database unavailable",
        "stage": "health",
        "request_id": "pool-timeout-123",
        "details": {},
    }}
    assert "private pool details" not in response.text


def test_health_does_not_classify_unexpected_error_as_database_failure(monkeypatch) -> None:
    def fail() -> None:
        raise RuntimeError("unrelated application bug")

    monkeypatch.setattr("app.api.health.check_database_connection", fail)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "unrelated application bug" not in response.text


@pytest.mark.parametrize("failure,code", [
    (OperationalError("SELECT secret_password", {}, Exception("private credentials")), "database_failed"),
    (RuntimeError("private application stack"), "internal_error"),
])
def test_ordinary_api_failure_has_structured_error_and_request_id(monkeypatch, failure, code) -> None:
    def fail(self):
        raise failure

    monkeypatch.setattr(ProjectService, "list", fail)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        supplied = client.get("/api/v1/projects", headers={"X-Request-ID": "audit-project-123"})
        generated = client.get("/api/v1/projects")
    for response in (supplied, generated):
        assert response.status_code == 500
        assert response.json()["error"]["code"] == code
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
        assert response.json()["error"]["details"] == {}
        assert "secret_password" not in response.text
        assert "private credentials" not in response.text
        assert "private application stack" not in response.text
    assert supplied.headers["X-Request-ID"] == "audit-project-123"
    assert UUID(generated.headers["X-Request-ID"])
