from io import BytesIO
from pathlib import Path
from datetime import timedelta

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.main import create_app
from app.persistence.base import Base
from app.persistence.database import get_session
from app.persistence.models import Paper, PaperChunk, now_utc
from app.services.chunking import chunk_pages
from app.services.papers import PaperService
from app.services.errors import ServiceError
from app.services.pdf import ExtractedPage, PDFQualityError, PDFService
from app.services.storage import LocalPDFStorage


def pdf_bytes(*pages: str) -> bytes:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


@pytest.fixture
def client(tmp_path: Path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
        storage_root=tmp_path,
        upload_size_limit_mb=1,
        request_body_limit_mb=2,
        page_limit=3,
    )
    app = create_app(settings)

    def session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as test_client:
        yield test_client, engine, tmp_path
    engine.dispose()


def create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"title": "材料研究", "research_question": "什么影响材料强度？"},
    )
    assert response.status_code == 201
    return response.json()


def test_project_api_revision_and_stale_update(client):
    api, _, _ = client
    project = create_project(api)
    assert project["question_revision"] == 1
    assert project["status"] == "active"
    assert api.get("/api/v1/projects").json() == [project]
    assert api.get(f"/api/v1/projects/{project['id']}").json() == project
    title = api.patch(
        f"/api/v1/projects/{project['id']}",
        json={"title": "新标题", "expected_question_revision": 1},
    )
    assert title.status_code == 200
    assert title.json()["question_revision"] == 1
    updated = api.patch(
        f"/api/v1/projects/{project['id']}",
        json={"research_question": "哪些变量影响强度？", "expected_question_revision": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["question_revision"] == 2
    stale = api.patch(
        f"/api/v1/projects/{project['id']}",
        json={"title": "过期更新", "expected_question_revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_revision"
    assert api.get(f"/api/v1/projects/{project['id']}").json()["title"] == "新标题"


def test_upload_isolation_safe_storage_and_original_retrieval(client):
    api, engine, root = client
    first = create_project(api)
    second = create_project(api)
    data = pdf_bytes("A valid paper page with enough text for extraction and chunking.")
    upload = api.post(
        f"/api/v1/projects/{first['id']}/papers",
        files={"file": ("../source.pdf", BytesIO(data), "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    paper = upload.json()
    assert paper["parse_status"] == "uploaded"
    assert paper["title"] is None
    assert paper["page_count"] == 1
    assert api.get(f"/api/v1/projects/{first['id']}/papers").json() == [paper]
    assert api.get(f"/api/v1/projects/{first['id']}/papers/{paper['id']}").json() == paper
    source = api.get(f"/api/v1/projects/{first['id']}/papers/{paper['id']}/file")
    assert source.status_code == 200
    assert source.content == data
    assert source.headers["content-type"] == "application/pdf"
    assert api.get(f"/api/v1/projects/{second['id']}/papers").json() == []
    assert api.get(f"/api/v1/projects/{second['id']}/papers/{paper['id']}").status_code == 404
    assert api.get(f"/api/v1/projects/{second['id']}/papers/{paper['id']}/file").status_code == 404
    with Session(engine) as session:
        stored = session.get(Paper, paper["id"])
        assert stored.storage_key.endswith(".pdf")
        assert ".." not in stored.storage_key
        assert "/" not in stored.storage_key
        assert (root / stored.storage_key).read_bytes() == data


def test_upload_rejects_invalid_and_limited_pdf(client):
    api, _, _ = client
    project = create_project(api)
    url = f"/api/v1/projects/{project['id']}/papers"
    invalid = api.post(url, files={"file": ("bad.pdf", b"not pdf", "application/pdf")})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_pdf"
    wrong_extension = api.post(url, files={"file": ("bad.txt", pdf_bytes("text"), "text/plain")})
    assert wrong_extension.status_code == 400
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Protected source text")
    encrypted_data = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="reader-password",
    )
    document.close()
    encrypted = api.post(url, files={"file": ("locked.pdf", encrypted_data, "application/pdf")})
    assert encrypted.status_code == 400
    assert encrypted.json()["error"]["code"] == "invalid_pdf"
    too_many_pages = api.post(url, files={"file": ("many.pdf", pdf_bytes("a", "b", "c", "d"), "application/pdf")})
    assert too_many_pages.status_code == 413
    assert too_many_pages.json()["error"]["code"] == "file_too_large"
    oversized = b"%PDF-" + b"x" * (1024 * 1024 + 1)
    response = api.post(url, files={"file": ("huge.pdf", oversized, "application/pdf")})
    assert response.status_code == 413
    assert api.get(f"/api/v1/projects/{project['id']}/papers").json() == []


def test_request_body_limit_rejects_before_paper_creation(client):
    api, engine, _ = client
    url = "/api/v1/projects/missing/papers"
    data = b"%PDF-" + b"x" * (2 * 1024 * 1024 + 1)
    response = api.post(url, files={"file": ("huge.pdf", data, "application/pdf")}, headers={"X-Request-ID": "body-123"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert response.json()["error"]["request_id"] == "body-123"
    assert response.headers["X-Request-ID"] == "body-123"
    with Session(engine) as session:
        assert session.scalars(select(Paper)).all() == []


def test_streamed_unknown_length_body_is_bounded(client):
    api, _, _ = client
    prefix = b'--test\r\nContent-Disposition: form-data; name="file"; filename="huge.pdf"\r\nContent-Type: application/pdf\r\n\r\n%PDF-'
    def chunks():
        yield prefix + b"x" * (1200 * 1024)
        yield b"y" * (1200 * 1024)
        yield b"\r\n--test--\r\n"
    response = api.post("/api/v1/projects/missing/papers", content=chunks(), headers={"Content-Type": "multipart/form-data; boundary=test", "X-Request-ID": "stream-123"})
    assert response.status_code == 413
    assert response.json()["error"]["request_id"] == "stream-123"


def test_stale_parse_recovers_without_duplicate_chunks(client):
    api, engine, root = client
    project = create_project(api)
    data = (Path(__file__).parent / "fixtures" / "two_pages.pdf").read_bytes()
    paper_id = api.post(f"/api/v1/projects/{project['id']}/papers", files={"file": ("source.pdf", data, "application/pdf")}).json()["id"]
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        paper.parse_status = "parsing"
        paper.parse_started_at = now_utc()
        session.commit()
        manager = PaperService(session, root, parse_stale_after_seconds=60)
        with pytest.raises(Exception) as fresh:
            manager.parse(paper_id)
        assert getattr(fresh.value, "code", None) == "invalid_state"
        paper.parse_started_at = now_utc() - timedelta(minutes=2)
        session.add(PaperChunk(paper_id=paper_id, page_number=1, chunk_index=0, text="orphan chunk"))
        session.commit()
        result = manager.parse(paper_id)
        assert result.parse_status == "parsed"
        chunks = session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id).order_by(PaperChunk.page_number, PaperChunk.chunk_index)).all()
        pages = PDFService().extract(data)
        assert chunks
        assert all(chunk.text in pages[chunk.page_number - 1].text for chunk in chunks)
        assert all("Second fixture page" not in chunk.text for chunk in chunks if chunk.page_number == 1)
        assert all("First fixture page" not in chunk.text for chunk in chunks if chunk.page_number == 2)
        assert "orphan chunk" not in [chunk.text for chunk in chunks]
        assert len({(c.page_number, c.chunk_index) for c in chunks}) == len(chunks)
        with pytest.raises(Exception) as parsed:
            manager.parse(paper_id)
        assert getattr(parsed.value, "code", None) == "invalid_state"


def test_parsing_without_start_time_cannot_be_claimed(client):
    api, engine, root = client
    project = create_project(api)
    data = (Path(__file__).parent / "fixtures" / "two_pages.pdf").read_bytes()
    paper_id = api.post(
        f"/api/v1/projects/{project['id']}/papers",
        files={"file": ("source.pdf", data, "application/pdf")},
    ).json()["id"]
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        paper.parse_status = "parsing"
        paper.parse_started_at = None
        session.commit()
        with pytest.raises(ServiceError) as error:
            PaperService(session, root).parse(paper_id)
        assert error.value.status_code == 409
        assert error.value.code == "invalid_state"
        session.refresh(paper)
        assert paper.parse_status == "parsing"
        assert paper.parse_started_at is None
        assert session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)).all() == []


def test_stale_recovery_failure_removes_orphan_chunks_and_keeps_pdf(client):
    api, engine, root = client
    project = create_project(api)
    data = pdf_bytes("A complete page of useful research text for recovery testing.")
    paper_id = api.post(f"/api/v1/projects/{project['id']}/papers", files={"file": ("source.pdf", data, "application/pdf")}).json()["id"]
    with Session(engine) as session:
        paper = session.get(Paper, paper_id)
        paper.parse_status = "parsing"
        paper.parse_started_at = now_utc() - timedelta(minutes=2)
        session.add(PaperChunk(paper_id=paper_id, page_number=1, chunk_index=0, text="orphan chunk"))
        session.commit()
        manager = PaperService(session, root, parse_stale_after_seconds=60)
        manager.pdf.extract = lambda data: (_ for _ in ()).throw(ValueError("extraction failed"))
        result = manager.parse(paper_id)
        assert result.parse_status == "parse_failed"
        assert session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper_id)).all() == []
        assert api.get(f"/api/v1/projects/{project['id']}/papers/{paper_id}/file").content == data


def test_upload_requires_existing_project_and_storage_rejects_traversal(client):
    api, _, root = client
    response = api.post(
        "/api/v1/projects/missing/papers",
        files={"file": ("sample.pdf", pdf_bytes("A valid page"), "application/pdf")},
    )
    assert response.status_code == 404
    assert not list(root.glob("*.pdf"))
    with pytest.raises(ValueError, match="Invalid storage key"):
        LocalPDFStorage(root).path_for("../outside.pdf")


def test_pdf_extraction_and_page_aware_chunking():
    pages = PDFService().extract(pdf_bytes(
        "First physical page has a factual sentence. Another fact follows.",
        "Second physical page contains different source text.",
    ))
    assert [page.page_number for page in pages] == [1, 2]
    chunks = chunk_pages(pages, chunk_size=42, overlap=0)
    assert [chunk.page_number for chunk in chunks] == [1, 1, 2, 2]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 0, 1]
    assert all(chunk.text in pages[chunk.page_number - 1].text for chunk in chunks)
    assert chunk_pages(pages, chunk_size=42, overlap=0) == chunks


def test_committed_synthetic_pdf_fixture_preserves_physical_pages():
    fixture = Path(__file__).parent / "fixtures" / "two_pages.pdf"
    pages = PDFService().extract(fixture.read_bytes())
    assert [page.page_number for page in pages] == [1, 2]
    assert "First fixture page" in pages[0].text
    assert "Second fixture page" in pages[1].text


def test_chunk_overlap_and_quality_rules_are_deterministic():
    pages = [ExtractedPage(1, "One short sentence. A second sentence. A third sentence.")]
    chunks = chunk_pages(pages, chunk_size=43, overlap=10)
    assert len(chunks) >= 2
    assert "A second sentence." in chunks[0].text
    assert "A second sentence." in chunks[1].text
    assert all(chunk.text in pages[0].text for chunk in chunks)
    with pytest.raises(PDFQualityError, match="Too many pages"):
        PDFService(min_extracted_chars=10, min_text_page_ratio=0.75).validate_quality([
            ExtractedPage(1, "This is enough text to reach the threshold."),
            ExtractedPage(2, ""),
        ])
    with pytest.raises(PDFQualityError, match="replacement"):
        PDFService(min_extracted_chars=10).validate_quality([
            ExtractedPage(1, "A long useful text about methods and results.\ufffd\ufffd\ufffd\ufffd\ufffd"),
        ])


def test_long_sentence_uses_word_boundaries_with_configured_size():
    source = " ".join(f"term{i}" for i in range(30))
    chunks = chunk_pages([ExtractedPage(1, source)], chunk_size=45, overlap=8)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 45 for chunk in chunks)
    assert all(chunk.text in source for chunk in chunks)


def test_parse_service_success_and_failure_states(client):
    api, engine, root = client
    project = create_project(api)
    data = pdf_bytes(
        "First page contains a complete useful paragraph about research methods.",
        "Second page contains another complete useful paragraph with source text.",
    )
    uploaded = api.post(
        f"/api/v1/projects/{project['id']}/papers",
        files={"file": ("source.pdf", data, "application/pdf")},
    ).json()
    with Session(engine) as session:
        paper = PaperService(session, root, chunk_size=80, chunk_overlap=10).parse(uploaded["id"])
        assert paper.parse_status == "parsed"
        chunks = session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id).order_by(PaperChunk.page_number, PaperChunk.chunk_index)).all()
        assert {chunk.page_number for chunk in chunks} == {1, 2}
        assert all(chunk.text in PDFService().extract(data)[chunk.page_number - 1].text for chunk in chunks)
    blank = api.post(
        f"/api/v1/projects/{project['id']}/papers",
        files={"file": ("scan.pdf", pdf_bytes("", ""), "application/pdf")},
    ).json()
    with Session(engine) as session:
        paper = PaperService(session, root).parse(blank["id"])
        assert paper.parse_status == "low_quality"
        assert paper.quality_note
        assert session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id)).all() == []
    broken = api.post(
        f"/api/v1/projects/{project['id']}/papers",
        files={"file": ("broken.pdf", data, "application/pdf")},
    ).json()
    with Session(engine) as session:
        paper = session.get(Paper, broken["id"])
        (root / paper.storage_key).write_bytes(b"broken after upload")
        result = PaperService(session, root).parse(paper.id)
        assert result.parse_status == "parse_failed"
        assert result.quality_note
        assert session.scalars(select(PaperChunk).where(PaperChunk.paper_id == paper.id)).all() == []
