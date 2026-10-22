from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import EvidenceCreate, EvidenceRead, EvidenceSelection
from app.persistence.database import get_session
from app.services.evidence import EvidenceService


router = APIRouter(prefix="/api/v1/projects/{project_id}/evidence", tags=["evidence"])


@router.post("", response_model=EvidenceRead, status_code=201)
def create_evidence(project_id: str, body: EvidenceCreate, session: Session = Depends(get_session)) -> dict[str, object]:
    return EvidenceService(session).create(
        project_id, body.paper_chunk_id, body.source_text, body.evidence_type,
        body.relevance_note, body.expected_question_revision,
    )


@router.get("", response_model=list[EvidenceRead])
def list_evidence(
    project_id: str, selection_status: Literal["selected", "rejected"] | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[dict[str, object]]:
    return EvidenceService(session).list(project_id, selection_status)


@router.get("/{evidence_id}", response_model=EvidenceRead)
def get_evidence(project_id: str, evidence_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    return EvidenceService(session).get(project_id, evidence_id)


@router.patch("/{evidence_id}/selection", response_model=EvidenceRead)
def select_evidence(
    project_id: str, evidence_id: str, body: EvidenceSelection, session: Session = Depends(get_session),
) -> dict[str, object]:
    return EvidenceService(session).select(
        project_id, evidence_id, body.selection_status, body.expected_question_revision,
    )
