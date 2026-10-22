from datetime import timedelta
from pathlib import Path

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.persistence.models import Paper, PaperChunk, Project, now_utc
from app.services.chunking import chunk_pages
from app.services.errors import ServiceError
from app.services.pdf import PDFError, PDFQualityError, PDFService
from app.services.storage import LocalPDFStorage


class PaperService:
    def __init__(
        self,
        session: Session,
        storage_root: Path,
        *,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
        min_extracted_chars: int = 30,
        min_text_page_ratio: float = 0.5,
        max_replacement_ratio: float = 0.05,
        parse_stale_after_seconds: int = 600,
    ):
        self.session = session
        self.storage = LocalPDFStorage(storage_root)
        self.pdf = PDFService(min_extracted_chars, min_text_page_ratio, max_replacement_ratio)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.parse_stale_after_seconds = parse_stale_after_seconds

    def _require_project(self, project_id: str) -> None:
        if self.session.get(Project, project_id) is None:
            raise ServiceError(404, "not_found", "Project not found", "paper")

    def upload(self, project_id: str, filename: str, data: bytes, page_limit: int) -> Paper:
        self._require_project(project_id)
        if not filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
            raise ServiceError(400, "invalid_pdf", "A valid PDF file is required", "paper_upload")
        try:
            page_count = self.pdf.page_count(data)
        except PDFError as exc:
            raise ServiceError(400, "invalid_pdf", str(exc), "paper_upload") from exc
        if page_count > page_limit:
            raise ServiceError(413, "file_too_large", "PDF exceeds the page limit", "paper_upload")
        safe_filename = Path(filename.replace("\\", "/")).name[:255]
        key = self.storage.save(data)
        try:
            paper = Paper(
                project_id=project_id,
                source_filename=safe_filename,
                storage_key=key,
                parse_status="uploaded",
                page_count=page_count,
            )
            self.session.add(paper)
            self.session.commit()
            self.session.refresh(paper)
            return paper
        except Exception:
            self.session.rollback()
            self.storage.remove(key)
            raise

    def list(self, project_id: str) -> list[Paper]:
        self._require_project(project_id)
        return list(self.session.scalars(
            select(Paper).where(Paper.project_id == project_id).order_by(Paper.created_at, Paper.id)
        ))

    def get(self, project_id: str, paper_id: str) -> Paper:
        self._require_project(project_id)
        paper = self.session.scalar(
            select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
        )
        if paper is None:
            raise ServiceError(404, "not_found", "Paper not found", "paper")
        return paper

    def file(self, project_id: str, paper_id: str) -> bytes:
        paper = self.get(project_id, paper_id)
        try:
            return self.storage.read(paper.storage_key)
        except FileNotFoundError as exc:
            raise ServiceError(404, "not_found", "Paper file not found", "paper") from exc

    def parse(self, paper_id: str) -> Paper:
        claimed_at = now_utc()
        cutoff = claimed_at - timedelta(seconds=self.parse_stale_after_seconds)
        claim = self.session.execute(
            update(Paper)
            .where(
                Paper.id == paper_id,
                or_(
                    Paper.parse_status == "uploaded",
                    and_(
                        Paper.parse_status == "parsing",
                        Paper.parse_started_at <= cutoff,
                    ),
                ),
            )
            .values(parse_status="parsing", parse_started_at=claimed_at, quality_note=None)
        )
        if claim.rowcount != 1:
            self.session.rollback()
            if self.session.get(Paper, paper_id) is None:
                raise ServiceError(404, "not_found", "Paper not found", "paper_parse")
            raise ServiceError(409, "invalid_state", "Paper is not awaiting parsing", "paper_parse")
        self.session.commit()
        paper = self.session.get(Paper, paper_id)
        try:
            pages = self.pdf.extract(self.storage.read(paper.storage_key))
            self.pdf.validate_quality(pages)
            drafts = chunk_pages(pages, self.chunk_size, self.chunk_overlap)
            if not drafts:
                raise PDFQualityError("No useful text chunks were produced")
            self._finish_claim(paper_id, claimed_at, "parsed", None)
            for draft in drafts:
                self.session.add(PaperChunk(
                    paper_id=paper.id,
                    page_number=draft.page_number,
                    chunk_index=draft.chunk_index,
                    section=draft.section,
                    text=draft.text,
                ))
            self.session.commit()
        except (PDFQualityError, PDFError, OSError, ValueError) as exc:
            self.session.rollback()
            self._finish_claim(
                paper_id, claimed_at,
                "low_quality" if isinstance(exc, PDFQualityError) else "parse_failed",
                str(exc)[:500],
            )
            self.session.commit()
        except ServiceError:
            self.session.rollback()
            raise
        except Exception:
            self.session.rollback()
            self._finish_claim(paper_id, claimed_at, "parse_failed", "Parsing or chunk persistence failed")
            self.session.commit()
            raise
        paper = self.session.get(Paper, paper_id)
        self.session.refresh(paper)
        return paper

    def _finish_claim(self, paper_id: str, claimed_at, status: str, note: str | None) -> None:
        result = self.session.execute(
            update(Paper)
            .where(Paper.id == paper_id, Paper.parse_status == "parsing", Paper.parse_started_at == claimed_at)
            .values(parse_status=status, quality_note=note)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ServiceError(409, "invalid_state", "Parsing claim was superseded", "paper_parse")
        self.session.execute(delete(PaperChunk).where(PaperChunk.paper_id == paper_id))
