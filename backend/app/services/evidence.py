"""User-selected evidence, anchored to immutable paper chunks."""

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.persistence.models import Evidence, Paper, PaperChunk, Project
from app.services.errors import ServiceError


class EvidenceService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _project(self, project_id: str, revision: int | None = None) -> Project:
        project = self.session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None:
            raise ServiceError(404, "not_found", "Project not found", "evidence")
        if revision is not None and project.question_revision != revision:
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "evidence")
        return project

    @staticmethod
    def _read(item: Evidence) -> dict[str, object]:
        chunk = item.chunk
        paper = chunk.paper
        return {
            "id": item.id, "project_id": item.project_id, "paper_chunk_id": item.paper_chunk_id,
            "paper_id": paper.id, "paper_title": paper.title, "source_filename": paper.source_filename,
            "page_number": chunk.page_number, "section": chunk.section, "source_text": item.source_text,
            "evidence_type": item.evidence_type, "relevance_note": item.relevance_note,
            "selection_status": item.selection_status, "question_revision": item.question_revision,
            "paper_file_url": f"/api/v1/projects/{item.project_id}/papers/{paper.id}/file",
        }

    def create(
        self, project_id: str, chunk_id: str, source_text: str, evidence_type: str,
        relevance_note: str | None, revision: int,
    ) -> dict[str, object]:
        project = self._project(project_id, revision)
        chunk = self.session.scalar(
            select(PaperChunk).join(Paper).where(
                PaperChunk.id == chunk_id, Paper.project_id == project_id,
                Paper.parse_status == "indexed",
            )
        )
        if chunk is None:
            raise ServiceError(422, "invalid_reference", "Chunk is not an indexed source in this project", "evidence")
        if source_text not in chunk.text:
            raise ServiceError(422, "invalid_provenance", "Source text is not in the selected PaperChunk", "evidence")
        item = Evidence(
            project_id=project.id, paper_chunk_id=chunk.id, source_text=source_text,
            evidence_type=evidence_type, relevance_note=relevance_note,
            selection_status="selected", question_revision=revision,
        )
        self.session.add(item)
        self.session.commit()
        self.session.refresh(item)
        return self._read(item)

    def list(self, project_id: str, selection_status: str | None = None) -> list[dict[str, object]]:
        self._project(project_id)
        query = select(Evidence).where(Evidence.project_id == project_id)
        if selection_status is not None:
            query = query.where(Evidence.selection_status == selection_status)
        query = query.options(joinedload(Evidence.chunk).joinedload(PaperChunk.paper)).order_by(Evidence.created_at, Evidence.id)
        return [self._read(item) for item in self.session.scalars(query)]

    def get(self, project_id: str, evidence_id: str) -> dict[str, object]:
        self._project(project_id)
        item = self.session.scalar(
            select(Evidence).where(Evidence.id == evidence_id, Evidence.project_id == project_id)
            .options(joinedload(Evidence.chunk).joinedload(PaperChunk.paper))
        )
        if item is None:
            raise ServiceError(404, "not_found", "Evidence not found", "evidence")
        return self._read(item)

    def select(self, project_id: str, evidence_id: str, selection_status: str, revision: int) -> dict[str, object]:
        project = self._project(project_id)
        item = self.session.scalar(
            select(Evidence).where(Evidence.id == evidence_id, Evidence.project_id == project_id)
            .with_for_update()
        )
        if item is None:
            raise ServiceError(404, "not_found", "Evidence not found", "evidence")
        if project.question_revision != revision:
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "evidence")
        if item.question_revision != revision:
            raise ServiceError(409, "invalid_state", "Evidence is not in the current question revision", "evidence")
        item.selection_status = selection_status
        self.session.commit()
        return self.get(project_id, evidence_id)
