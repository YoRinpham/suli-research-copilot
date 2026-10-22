"""Real PostgreSQL and pgvector M2 acceptance checks."""

import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.main import create_app
from app.persistence.database import get_engine
from app.persistence.models import Evidence, Paper, PaperChunk, Project, now_utc
from app.providers.embeddings import EmbeddingError
from app.services.pdf import PDFService


pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_INTEGRATION") != "1", reason="Requires migrated PostgreSQL")


class Vectors:
    model_name = "qwen3.7-text-embedding"
    dimensions = 1024

    def __init__(self):
        self.document_calls = 0
        self.fail = False

    def embed_documents(self, texts):
        self.document_calls += 1
        if self.fail:
            raise EmbeddingError("controlled failure")
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    @staticmethod
    def _vector(text):
        if "First" in text or "first" in text:
            return [1.0, 0.0] + [0.0] * 1022
        return [0.0, 1.0] + [0.0] * 1022


@pytest.fixture
def live_m2():
    ids = []
    source = (Path(__file__).parent / "fixtures" / "two_pages.pdf").read_bytes()
    with TemporaryDirectory(prefix="research-copilot-m2-") as directory:
        app = create_app(get_settings().model_copy(update={"storage_root": Path(directory)}))
        provider = Vectors()
        app.state.embedding_provider = provider
        with TestClient(app) as client:
            def project(title):
                response = client.post("/api/v1/projects", json={"title": title, "research_question": "first page"})
                assert response.status_code == 201, response.text
                ids.append(response.json()["id"])
                return response.json()

            def upload(project_id):
                response = client.post(f"/api/v1/projects/{project_id}/papers", files={"file": ("two_pages.pdf", source, "application/pdf")})
                assert response.status_code == 201, response.text
                return response.json()

            try:
                yield client, provider, project, upload, source
            finally:
                with Session(get_engine()) as session:
                    session.execute(delete(Evidence).where(Evidence.project_id.in_(ids)))
                    paper_ids = list(session.scalars(select(Paper.id).where(Paper.project_id.in_(ids))))
                    if paper_ids:
                        session.execute(delete(PaperChunk).where(PaperChunk.paper_id.in_(paper_ids)))
                    session.execute(delete(Paper).where(Paper.project_id.in_(ids)))
                    session.execute(delete(Project).where(Project.id.in_(ids)))
                    session.commit()


def test_end_to_end_pgvector_retrieval_evidence_and_isolation(live_m2):
    client, _, project, upload, source = live_m2
    a, b = project("A"), project("B")
    a_paper, b_paper = upload(a["id"]), upload(b["id"])
    for owner, paper in ((a, a_paper), (b, b_paper)):
        indexed = client.post(f"/api/v1/projects/{owner['id']}/papers/{paper['id']}/index")
        assert indexed.status_code == 200, indexed.text
        assert indexed.json()["parse_status"] == "indexed"
    with Session(get_engine()) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == a_paper["id"])))
        assert len(chunks) == 2
        assert all(len(chunk.embedding) == 1024 and chunk.embedding_model == "qwen3.7-text-embedding" for chunk in chunks)
        for status in ("parse_failed", "low_quality", "parsed"):
            excluded = Paper(project_id=a["id"], source_filename=f"{status}.pdf", storage_key=f"{status}-{uuid4()}.pdf", parse_status=status)
            session.add(excluded)
            session.flush()
            session.add(PaperChunk(
                paper_id=excluded.id, page_number=1, chunk_index=0, text="First excluded text",
                embedding=Vectors._vector("First"), embedding_model="qwen3.7-text-embedding", embedding_dimension=1024,
            ))
        session.commit()
    retrieval_path = f"/api/v1/projects/{a['id']}/retrievals"
    response = client.post(retrieval_path, json={"query": "first", "top_k": 2, "expected_question_revision": 1})
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [item["page_number"] for item in results] == [1, 2]
    assert results[0]["similarity_score"] > results[1]["similarity_score"]
    assert {item["paper_id"] for item in results} == {a_paper["id"]}
    assert client.post(retrieval_path, json={"top_k": 1, "expected_question_revision": 1}).json()["results"] == results[:1]
    selected = client.post(f"/api/v1/projects/{a['id']}/evidence", json={
        "paper_chunk_id": results[0]["chunk_id"], "source_text": results[0]["text"],
        "evidence_type": "reported_result", "expected_question_revision": 1,
    })
    assert selected.status_code == 201, selected.text
    evidence = selected.json()
    assert evidence["selection_status"] == "selected"
    assert evidence["page_number"] == 1
    assert selected.headers["X-Request-ID"]
    assert client.get(evidence["paper_file_url"]).content == source
    with Session(get_engine()) as session:
        chunk = session.get(PaperChunk, evidence["paper_chunk_id"])
        assert evidence["source_text"] in chunk.text
        assert chunk.text in PDFService().extract(source)[chunk.page_number - 1].text
        assert chunk.paper_id == evidence["paper_id"]
    cross = client.post(f"/api/v1/projects/{b['id']}/evidence", json={
        "paper_chunk_id": results[0]["chunk_id"], "source_text": results[0]["text"],
        "evidence_type": "other", "expected_question_revision": 1,
    })
    assert cross.status_code == 422
    assert client.get(f"/api/v1/projects/{b['id']}/evidence/{evidence['id']}").status_code == 404
    with Session(get_engine()) as session:
        session.get(Paper, a_paper["id"]).parse_status = "embedding_failed"
        session.commit()
    only_b = client.post(retrieval_path, json={"expected_question_revision": 1})
    assert only_b.status_code == 424
    with Session(get_engine()) as session:
        session.get(Paper, a_paper["id"]).parse_status = "indexed"
        for chunk in session.scalars(select(PaperChunk).where(PaperChunk.paper_id == a_paper["id"])):
            chunk.embedding_model = "another-1024-model"
        session.commit()
    incompatible = client.post(retrieval_path, json={"expected_question_revision": 1})
    assert incompatible.status_code == 424


def test_concurrent_claim_and_stale_recovery_on_postgresql(live_m2):
    client, provider, project, upload, _ = live_m2
    owner = project("Concurrency")
    paper = upload(owner["id"])
    path = f"/api/v1/projects/{owner['id']}/papers/{paper['id']}/index"
    claimed, release = Event(), Event()

    class HeldVectors(Vectors):
        def embed_documents(self, texts):
            claimed.set()
            assert release.wait(10)
            return super().embed_documents(texts)

    client.app.state.embedding_provider = HeldVectors()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, path)
        try:
            assert claimed.wait(10)
            second = client.post(path)
            assert second.status_code == 409
            assert second.json()["error"]["code"] == "invalid_state"
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
    with Session(get_engine()) as session:
        chunk_ids = list(session.scalars(select(PaperChunk.id).where(PaperChunk.paper_id == paper["id"])))
        row = session.get(Paper, paper["id"])
        row.parse_status = "embedding"
        row.embedding_started_at = now_utc() - timedelta(seconds=1200)
        for chunk in session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])):
            chunk.embedding_model = "old-1024-model"
        session.commit()
    client.app.state.embedding_provider = provider
    assert client.post(path).status_code == 200
    with Session(get_engine()) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        assert sorted(chunk.id for chunk in chunks) == sorted(chunk_ids)
        assert {chunk.embedding_model for chunk in chunks} == {"qwen3.7-text-embedding"}


def test_provider_failure_retry_reuses_chunks_on_postgresql(live_m2):
    client, provider, project, upload, _ = live_m2
    owner = project("Retry")
    paper = upload(owner["id"])
    path = f"/api/v1/projects/{owner['id']}/papers/{paper['id']}/index"

    class FailingVectors(Vectors):
        def embed_documents(self, texts):
            raise EmbeddingError("controlled failure")

    client.app.state.embedding_provider = FailingVectors()
    response = client.post(path)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "embedding_failed"
    with Session(get_engine()) as session:
        assert session.get(Paper, paper["id"]).parse_status == "embedding_failed"
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        ids = {chunk.id for chunk in chunks}
        assert all(chunk.embedding is None for chunk in chunks)
    client.app.state.embedding_provider = provider
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 200
    with Session(get_engine()) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        assert {chunk.id for chunk in chunks} == ids
        assert all(len(chunk.embedding) == 1024 for chunk in chunks)


def test_old_model_reindex_is_atomic_and_preserves_evidence_on_postgresql(live_m2):
    client, provider, project, upload, _ = live_m2
    owner = project("Model migration")
    paper = upload(owner["id"])
    path = f"/api/v1/projects/{owner['id']}/papers/{paper['id']}/index"
    assert client.post(path).status_code == 200
    with Session(get_engine()) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        chunk_ids = {chunk.id for chunk in chunks}
        for chunk in chunks:
            chunk.embedding_model = "text-embedding-v4"
        session.commit()
        source_chunk_id, source_text = chunks[0].id, chunks[0].text[:20]
    evidence = client.post(f"/api/v1/projects/{owner['id']}/evidence", json={
        "paper_chunk_id": source_chunk_id, "source_text": source_text,
        "evidence_type": "reported_result", "expected_question_revision": 1,
    }).json()
    before_calls = provider.document_calls
    provider.fail = True
    failed = client.post(path)
    assert failed.status_code == 502
    with Session(get_engine()) as session:
        assert session.get(Paper, paper["id"]).parse_status == "embedding_failed"
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        assert {chunk.embedding_model for chunk in chunks} == {"text-embedding-v4"}
        stored = session.get(Evidence, evidence["id"])
        assert (stored.source_text, stored.paper_chunk_id, stored.question_revision) == (
            evidence["source_text"], evidence["paper_chunk_id"], evidence["question_revision"],
        )
    unavailable = client.post(f"/api/v1/projects/{owner['id']}/retrievals", json={
        "expected_question_revision": 1,
    })
    assert unavailable.status_code == 424
    provider.fail = False
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 200
    assert provider.document_calls == before_calls + 2
    with Session(get_engine()) as session:
        chunks = list(session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper["id"])))
        assert {chunk.id for chunk in chunks} == chunk_ids
        assert {chunk.embedding_model for chunk in chunks} == {provider.model_name}
        assert all(chunk.embedding_dimension == 1024 for chunk in chunks)
    after = client.get(f"/api/v1/projects/{owner['id']}/evidence/{evidence['id']}").json()
    for field in ("source_text", "paper_chunk_id", "page_number", "question_revision"):
        assert after[field] == evidence[field]


def test_exact_cosine_scores_and_bidirectional_project_isolation_on_postgresql(live_m2):
    client, _, project, _, _ = live_m2
    a, b = project("Vector A"), project("Vector B")
    axes = {
        "A identical": [1.0, 0.0] + [0.0] * 1022,
        "A sixty": [0.5, math.sqrt(3) / 2] + [0.0] * 1022,
        "A orthogonal": [0.0, 1.0] + [0.0] * 1022,
        "B exclusive": [1.0, 0.0] + [0.0] * 1022,
    }
    ids = {}
    with Session(get_engine()) as session:
        for owner, labels in ((a, ("A identical", "A sixty", "A orthogonal")), (b, ("B exclusive",))):
            paper = Paper(project_id=owner["id"], source_filename="synthetic.pdf",
                          storage_key=f"synthetic-{uuid4()}.pdf", parse_status="indexed")
            session.add(paper)
            session.flush()
            for index, label in enumerate(labels):
                chunk = PaperChunk(paper_id=paper.id, page_number=1, chunk_index=index,
                                   text=label, embedding=axes[label],
                                   embedding_model="qwen3.7-text-embedding", embedding_dimension=1024)
                session.add(chunk)
                session.flush()
                ids[label] = chunk.id
        session.commit()
    for owner, expected in ((a, ["A identical", "A sixty", "A orthogonal"]), (b, ["B exclusive"])):
        response = client.post(f"/api/v1/projects/{owner['id']}/retrievals", json={
            "query": "first", "top_k": 3, "expected_question_revision": 1,
        })
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        assert [result["text"] for result in results] == expected
        if owner == a:
            assert [result["similarity_score"] for result in results] == pytest.approx([1.0, 0.5, 0.0], abs=1e-5)
    for owner, foreign in ((a, "B exclusive"), (b, "A identical")):
        cross = client.post(f"/api/v1/projects/{owner['id']}/evidence", json={
            "paper_chunk_id": ids[foreign], "source_text": foreign,
            "evidence_type": "other", "expected_question_revision": 1,
        })
        assert cross.status_code == 422


def test_clean_base_to_m2_head_migration() -> None:
    configured = make_url(get_settings().database_url)
    admin_url = configured.set(database="postgres").render_as_string(hide_password=False)
    admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    database_name = "m2_clean_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        database_url = configured.set(database=database_name).render_as_string(hide_password=False)
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        root = Path(__file__).parents[1]
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0004_m2_embedding_evidence"], cwd=root, env=env, check=True, capture_output=True)
        current = subprocess.run([sys.executable, "-m", "alembic", "current"], cwd=root, env=env, check=True, capture_output=True, text=True)
        assert "0004_m2_embedding_evidence" in current.stdout
        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"))
                assert connection.scalar(text("SELECT atttypmod FROM pg_attribute WHERE attrelid = 'paper_chunks'::regclass AND attname = 'embedding'")) == 1024
        finally:
            engine.dispose()
    finally:
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
