from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.papers import embedding_provider
from app.api.schemas import RetrievalRequest, RetrievalResponse
from app.config.settings import Settings
from app.persistence.database import get_session
from app.services.retrieval import RetrievalService


router = APIRouter(prefix="/api/v1/projects/{project_id}/retrievals", tags=["retrieval"])


@router.post("", response_model=RetrievalResponse)
def retrieve(project_id: str, body: RetrievalRequest, request: Request, session: Session = Depends(get_session)) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return RetrievalService(
        session, embedding_provider(request), settings.retrieval_top_k, settings.retrieval_max_top_k,
    ).retrieve(project_id, body.query, body.top_k, body.expected_question_revision)
