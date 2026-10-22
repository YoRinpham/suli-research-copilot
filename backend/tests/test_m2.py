from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.main import create_app
from app.persistence.base import Base
from app.persistence.database import get_session
from app.persistence.models import Evidence, Paper, PaperChunk, now_utc
from app.providers.embeddings import EmbeddingError


class FakeEmbeddings:
    model_name = "qwen3.7-text-embedding"
    dimensions = 1024

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def embed_documents(self, texts):
        self.calls += 1
        if self.fail:
            raise EmbeddingError("test provider failure")
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * 1023


@pytest.fixture
def m2(tmp_path: Path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://x:x@localhost/x", storage_root=tmp_path)
    app = create_app(settings)
    fake = FakeEmbeddings()
    app.state.embedding_provider = fake

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as client:
        yield client, engine, fake
    engine.dispose()


def seeded(m2):
    client, engine, _ = m2
    project = client.post("/api/v1/projects", json={"title": "Test", "research_question": "What changes?"}).json()
    with Session(engine) as session:
        paper = Paper(project_id=project["id"], source_filename="source.pdf", storage_key="source.pdf", parse_status="parsed")
        session.add(paper)
        session.flush()
        chunk = PaperChunk(paper_id=paper.id, page_number=2, chunk_index=0, text="The exact source passage is here.")
        session.add(chunk)
        session.commit()
        return project["id"], paper.id, chunk.id


def test_index_failure_retry_and_idempotence(m2):
    client, engine, fake = m2
    project_id, paper_id, chunk_id = seeded(m2)
    path = f"/api/v1/projects/{project_id}/papers/{paper_id}/index"
    fake.fail = True
    failed = client.post(path)
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "embedding_failed"
    with Session(engine) as session:
        assert session.get(Paper, paper_id).parse_status == "embedding_failed"
        assert session.get(PaperChunk, chunk_id).embedding is None
    fake.fail = False
    indexed = client.post(path)
    assert indexed.status_code == 200, indexed.text
    assert indexed.json()["parse_status"] == "indexed"
    assert client.post(path).status_code == 200
    assert fake.calls == 2
    with Session(engine) as session:
        chunk = session.get(PaperChunk, chunk_id)
        assert chunk.embedding_model == fake.model_name
        assert chunk.embedding_dimension == 1024
        assert len(chunk.embedding) == 1024
        assert len(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)).all()) == 1


def test_embedding_claim_fresh_and_stale_recovery(m2):
    client, engine, fake = m2
    project_id, paper_id, chunk_id = seeded(m2)
    path = f"/api/v1/projects/{project_id}/papers/{paper_id}/index"
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        paper.parse_status = "embedding"
        paper.embedding_started_at = now_utc()
        session.commit()
    assert client.post(path).status_code == 409
    assert fake.calls == 0
    with Session(engine) as session:
        session.get(Paper, paper_id).embedding_started_at = None
        session.commit()
    assert client.post(path).status_code == 409
    with Session(engine) as session:
        session.get(Paper, paper_id).embedding_started_at = now_utc() - timedelta(seconds=1200)
        session.commit()
    assert client.post(path).status_code == 200
    with Session(engine) as session:
        assert session.get(PaperChunk, chunk_id).embedding_model == fake.model_name


def test_wrong_vector_dimension_fails_without_partial_index(m2):
    client, engine, fake = m2
    project_id, paper_id, chunk_id = seeded(m2)
    fake.embed_documents = lambda texts: [[1.0]]
    response = client.post(f"/api/v1/projects/{project_id}/papers/{paper_id}/index")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_failed"
    with Session(engine) as session:
        assert session.get(Paper, paper_id).parse_status == "embedding_failed"
        assert session.get(PaperChunk, chunk_id).embedding is None


def test_evidence_exact_provenance_selection_and_isolation(m2):
    client, engine, _ = m2
    project_id, paper_id, chunk_id = seeded(m2)
    assert client.post(f"/api/v1/projects/{project_id}/papers/{paper_id}/index").status_code == 200
    path = f"/api/v1/projects/{project_id}/evidence"
    body = {"paper_chunk_id": chunk_id, "source_text": "exact source passage", "evidence_type": "reported_result", "expected_question_revision": 1}
    invalid = client.post(path, json={**body, "source_text": "invented text"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_provenance"
    created = client.post(path, json=body)
    assert created.status_code == 201, created.text
    evidence = created.json()
    assert evidence["selection_status"] == "selected"
    assert evidence["page_number"] == 2
    assert evidence["paper_id"] == paper_id
    assert evidence["paper_file_url"].endswith(f"/papers/{paper_id}/file")
    assert client.get(path).json() == [evidence]
    assert client.get(f"{path}/{evidence['id']}").json() == evidence
    rejected = client.patch(f"{path}/{evidence['id']}/selection", json={"selection_status": "rejected", "expected_question_revision": 1})
    assert rejected.status_code == 200
    assert rejected.json()["source_text"] == evidence["source_text"]
    with Session(engine) as session:
        assert session.get(Evidence, evidence["id"]).source_text == body["source_text"]
    selected_again = client.patch(f"{path}/{evidence['id']}/selection", json={"selection_status": "selected", "expected_question_revision": 1})
    assert selected_again.json()["selection_status"] == "selected"
    changed = client.patch(f"/api/v1/projects/{project_id}", json={"research_question": "New question?", "expected_question_revision": 1})
    assert changed.status_code == 200
    obsolete = client.patch(f"{path}/{evidence['id']}/selection", json={
        "selection_status": "rejected", "expected_question_revision": 2,
    })
    assert obsolete.status_code == 409
    assert obsolete.json()["error"]["code"] == "invalid_state"
    assert client.get(f"{path}/{evidence['id']}").json()["question_revision"] == 1
    other = client.post("/api/v1/projects", json={"title": "Other", "research_question": "Other?"}).json()["id"]
    assert client.post(f"/api/v1/projects/{other}/evidence", json=body).status_code == 422
    assert client.get(f"/api/v1/projects/{other}/evidence/{evidence['id']}").status_code == 404

    for evidence_id in (evidence["id"], "missing-evidence"):
        missing = client.patch(
            f"/api/v1/projects/{other}/evidence/{evidence_id}/selection",
            json={"selection_status": "rejected", "expected_question_revision": 1},
            headers={"X-Request-ID": "selection-isolation"},
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        assert missing.json()["error"]["request_id"] == "selection-isolation"
    nonexistent = client.patch(
        f"{path}/missing-evidence/selection",
        json={"selection_status": "rejected", "expected_question_revision": 2},
    )
    assert nonexistent.status_code == 404


def test_indexed_model_upgrade_failure_retry_and_evidence_preservation(m2):
    client, engine, fake = m2
    project_id, paper_id, chunk_id = seeded(m2)
    path = f"/api/v1/projects/{project_id}/papers/{paper_id}/index"
    with Session(engine) as session:
        session.add(PaperChunk(
            paper_id=paper_id, page_number=2, chunk_index=1, text="A second source passage.",
            embedding=[0.0, 1.0] + [0.0] * 1022,
            embedding_model="text-embedding-v4", embedding_dimension=1024,
        ))
        chunk = session.get(PaperChunk, chunk_id)
        chunk.embedding = [1.0] + [0.0] * 1023
        chunk.embedding_model = "text-embedding-v4"
        chunk.embedding_dimension = 1024
        session.get(Paper, paper_id).parse_status = "indexed"
        session.commit()
        chunk_ids = {item.id for item in session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id))}
    evidence = client.post(f"/api/v1/projects/{project_id}/evidence", json={
        "paper_chunk_id": chunk_id, "source_text": "exact source passage",
        "evidence_type": "reported_result", "expected_question_revision": 1,
    }).json()
    fake.fail = True
    failed = client.post(path)
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "embedding_failed"
    with Session(engine) as session:
        assert session.get(Paper, paper_id).parse_status == "embedding_failed"
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)))
        assert {item.id for item in chunks} == chunk_ids
        assert {item.embedding_model for item in chunks} == {"text-embedding-v4"}
        assert session.get(Evidence, evidence["id"]).source_text == evidence["source_text"]
    fake.fail = False
    succeeded = client.post(path)
    assert succeeded.status_code == 200, succeeded.text
    assert succeeded.json()["parse_status"] == "indexed"
    assert client.post(path).status_code == 200
    assert fake.calls == 2
    with Session(engine) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)))
        assert {item.id for item in chunks} == chunk_ids
        assert {item.embedding_model for item in chunks} == {fake.model_name}
        assert all(item.embedding_dimension == 1024 for item in chunks)
        stored = session.get(Evidence, evidence["id"])
        assert stored.source_text == evidence["source_text"]
        assert stored.paper_chunk_id == chunk_id
        assert stored.question_revision == evidence["question_revision"]
    assert client.get(f"/api/v1/projects/{project_id}/evidence/{evidence['id']}").json()["page_number"] == 2


def test_incomplete_current_model_index_is_reembedded(m2):
    client, engine, fake = m2
    project_id, paper_id, chunk_id = seeded(m2)
    with Session(engine) as session:
        chunk = session.get(PaperChunk, chunk_id)
        chunk.embedding = [1.0] + [0.0] * 1023
        chunk.embedding_model = fake.model_name
        chunk.embedding_dimension = 1024
        session.add(PaperChunk(paper_id=paper_id, page_number=2, chunk_index=1,
                               text="Missing vector for second source."))
        session.get(Paper, paper_id).parse_status = "indexed"
        session.commit()
    response = client.post(f"/api/v1/projects/{project_id}/papers/{paper_id}/index")
    assert response.status_code == 200
    assert fake.calls == 1
    with Session(engine) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)))
        assert len(chunks) == 2
        assert all(chunk.embedding_model == fake.model_name for chunk in chunks)


def test_retrieval_rejects_stale_revision_and_unbounded_top_k(m2):
    client, _, _ = m2
    project_id, _, _ = seeded(m2)
    path = f"/api/v1/projects/{project_id}/retrievals"
    stale = client.post(path, json={"expected_question_revision": 2})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_revision"
    too_many = client.post(path, json={"expected_question_revision": 1, "top_k": 100})
    assert too_many.status_code == 422
    empty = client.post(path, json={"expected_question_revision": 1})
    assert empty.status_code == 424
    assert empty.json()["error"]["code"] == "no_relevant_chunks"
