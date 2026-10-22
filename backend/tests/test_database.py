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

from app.persistence.database import check_database_connection, get_engine
from app.config.settings import get_settings
from app.main import create_app
from app.persistence.models import Paper, PaperChunk, Project, now_utc
from app.services.papers import PaperService
from app.services.pdf import PDFService
from app.services.errors import ServiceError


def test_database_engine_uses_configured_postgresql_url() -> None:
    engine = get_engine()
    assert engine.url.drivername == "postgresql+psycopg"
    assert engine.url.render_as_string(hide_password=False) == get_settings().database_url


@pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Requires a running PostgreSQL database with pgvector",
)
def test_live_database_has_vector_extension() -> None:
    check_database_connection(require_vector=True)


@pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Requires an M1-migrated PostgreSQL database",
)
def test_live_m1_project_upload_parse_and_source_retrieval() -> None:
    source = (Path(__file__).parent / "fixtures" / "two_pages.pdf").read_bytes()
    with TemporaryDirectory(prefix="research-copilot-m1-") as directory:
        settings = get_settings().model_copy(update={"storage_root": Path(directory)})
        with TestClient(create_app(settings)) as client:
            created = client.post(
                "/api/v1/projects",
                json={"title": "M1 database smoke", "research_question": "How does the source text differ by page?"},
            )
            assert created.status_code == 201, created.text
            project_id = created.json()["id"]
            project_ids = [project_id]
            paper_ids = []
            try:
                older = client.post(
                    "/api/v1/projects",
                    json={"title": "M1 existing-project probe", "research_question": "Does listing include this project?"},
                )
                assert older.status_code == 201, older.text
                older_id = older.json()["id"]
                project_ids.append(older_id)
                with Session(get_engine()) as session:
                    prior = session.get(Project, older_id)
                    prior.created_at = now_utc() - timedelta(days=1)
                    session.commit()
                listed = client.get("/api/v1/projects")
                assert listed.status_code == 200
                matching = [item for item in listed.json() if item["id"] == project_id]
                assert matching == [created.json()]
                upload = client.post(
                    f"/api/v1/projects/{project_id}/papers",
                    files={"file": ("two_pages.pdf", source, "application/pdf")},
                )
                assert upload.status_code == 201, upload.text
                paper_id = upload.json()["id"]
                paper_ids.append(paper_id)
                assert client.get(f"/api/v1/projects/{project_id}/papers/{paper_id}/file").content == source
                with Session(get_engine()) as session:
                    paper = PaperService(session, Path(directory)).parse(paper_id)
                    assert paper.parse_status == "parsed"
                    chunks = session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)).all()
                    assert {chunk.page_number for chunk in chunks} == {1, 2}
                    pages = PDFService().extract(source)
                    assert all(chunk.text in pages[chunk.page_number - 1].text for chunk in chunks)
                    assert all("Second fixture page" not in c.text for c in chunks if c.page_number == 1)
                    assert all("First fixture page" not in c.text for c in chunks if c.page_number == 2)

                interrupted = client.post(
                    f"/api/v1/projects/{project_id}/papers",
                    files={"file": ("two_pages.pdf", source, "application/pdf")},
                )
                assert interrupted.status_code == 201, interrupted.text
                stale_id = interrupted.json()["id"]
                paper_ids.append(stale_id)
                with Session(get_engine()) as session:
                    stale = session.get(Paper, stale_id)
                    stale.parse_status = "parsing"
                    stale.parse_started_at = now_utc() - timedelta(seconds=1200)
                    session.add(PaperChunk(paper_id=stale_id, page_number=1, chunk_index=0, text="orphan chunk"))
                    session.commit()

                with Session(get_engine()) as session:
                    recovered = PaperService(session, Path(directory), parse_stale_after_seconds=600).parse(stale_id)
                    assert recovered.parse_status == "parsed"
                    chunks = session.scalars(select(PaperChunk).where(PaperChunk.paper_id == stale_id)).all()
                    assert chunks and all(c.text in pages[c.page_number - 1].text for c in chunks)
                    assert "orphan chunk" not in [c.text for c in chunks]
                    assert len({(c.page_number, c.chunk_index) for c in chunks}) == len(chunks)

                concurrent = client.post(
                    f"/api/v1/projects/{project_id}/papers",
                    files={"file": ("two_pages.pdf", source, "application/pdf")},
                )
                assert concurrent.status_code == 201, concurrent.text
                concurrent_id = concurrent.json()["id"]
                paper_ids.append(concurrent_id)
                claimed = Event()
                release = Event()

                def first_parse():
                    with Session(get_engine()) as session:
                        manager = PaperService(session, Path(directory))
                        extract = manager.pdf.extract

                        def held_extract(data):
                            claimed.set()
                            assert release.wait(5)
                            return extract(data)

                        manager.pdf.extract = held_extract
                        return manager.parse(concurrent_id).parse_status

                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(first_parse)
                    try:
                        assert claimed.wait(5)
                        with Session(get_engine()) as session:
                            with pytest.raises(ServiceError) as active:
                                PaperService(session, Path(directory)).parse(concurrent_id)
                            assert active.value.code == "invalid_state"
                    finally:
                        release.set()
                    assert future.result(timeout=5) == "parsed"
            finally:
                with Session(get_engine()) as session:
                    if paper_ids:
                        session.execute(delete(PaperChunk).where(PaperChunk.paper_id.in_(paper_ids)))
                        session.execute(delete(Paper).where(Paper.id.in_(paper_ids)))
                    session.execute(delete(Project).where(Project.id.in_(project_ids)))
                    session.commit()


@pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="Requires permission to create a disposable PostgreSQL database",
)
def test_migration_backfills_active_parse_claim() -> None:
    backend_root = Path(__file__).parents[1]
    configured = make_url(get_settings().database_url)
    admin_url = configured.set(database="postgres").render_as_string(hide_password=False)
    admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    database_name = "m1_parse_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    engine = None
    try:
        database_url = configured.set(database=database_name).render_as_string(hide_password=False)
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url

        def alembic(*arguments: str) -> str:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", *arguments],
                cwd=backend_root, env=env, capture_output=True, text=True, check=True,
            )
            return result.stdout + result.stderr

        alembic("upgrade", "0002_m1_project_papers")
        engine = create_engine(database_url)
        project_id, paper_id = str(uuid4()), str(uuid4())
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO projects (id, title, research_question, question_revision, status, created_at, updated_at)
                VALUES (:id, 'migration probe', 'Can active parsing stay protected?', 1, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": project_id})
            connection.execute(text("""
                INSERT INTO papers (id, project_id, source_filename, storage_key, parse_status, created_at)
                VALUES (:id, :project_id, 'fixture.pdf', :storage_key, 'parsing', CURRENT_TIMESTAMP)
            """), {"id": paper_id, "project_id": project_id, "storage_key": str(uuid4()) + ".pdf"})
        alembic("upgrade", "head")
        assert "0010_m6_report_delivery" in alembic("current")
        with Session(engine) as session:
            paper = session.get(Paper, paper_id)
            assert paper.parse_status == "parsing"
            assert paper.parse_started_at is not None
            with pytest.raises(ServiceError) as error:
                PaperService(session, Path("unused")).parse(paper_id)
            assert error.value.code == "invalid_state"
            assert error.value.status_code == 409
    finally:
        if engine is not None:
            engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
