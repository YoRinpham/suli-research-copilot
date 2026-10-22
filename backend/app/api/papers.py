from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.schemas import PaperRead
from app.config.settings import Settings
from app.persistence.database import get_session
from app.persistence.models import Paper
from app.services.errors import ServiceError
from app.services.papers import PaperService
from app.services.indexing import IndexingService
from app.providers.embeddings import DashScopeEmbeddingProvider


router = APIRouter(prefix="/api/v1/projects/{project_id}/papers", tags=["papers"])


def service(request: Request, session: Session) -> PaperService:
    settings: Settings = request.app.state.settings
    return PaperService(
        session,
        settings.storage_root,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        min_extracted_chars=settings.min_extracted_chars,
        min_text_page_ratio=settings.min_text_page_ratio,
        max_replacement_ratio=settings.max_replacement_ratio,
        parse_stale_after_seconds=settings.parse_stale_after_seconds,
    )


def embedding_provider(request: Request):
    if not hasattr(request.app.state, "embedding_provider"):
        settings: Settings = request.app.state.settings
        request.app.state.embedding_provider = DashScopeEmbeddingProvider(
            settings.qwen_api_key.get_secret_value() if settings.qwen_api_key else "",
            settings.embedding_model, settings.provider_timeout_seconds, settings.retry_count,
            base_url=settings.embedding_base_url,
        )
    return request.app.state.embedding_provider


@router.post("", response_model=PaperRead, status_code=201)
def upload_paper(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> Paper:
    manager = service(request, session)
    manager._require_project(project_id)
    settings: Settings = request.app.state.settings
    limit = settings.upload_size_limit_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise ServiceError(413, "file_too_large", "PDF exceeds the upload size limit", "paper_upload")
    return manager.upload(project_id, file.filename or "", data, settings.page_limit)


@router.get("", response_model=list[PaperRead])
def list_papers(project_id: str, request: Request, session: Session = Depends(get_session)) -> list[Paper]:
    return service(request, session).list(project_id)


@router.get("/{paper_id}", response_model=PaperRead)
def get_paper(project_id: str, paper_id: str, request: Request, session: Session = Depends(get_session)) -> Paper:
    return service(request, session).get(project_id, paper_id)


@router.post("/{paper_id}/index", response_model=PaperRead)
def index_paper(project_id: str, paper_id: str, request: Request, session: Session = Depends(get_session)) -> Paper:
    settings: Settings = request.app.state.settings
    return IndexingService(
        session, service(request, session), embedding_provider(request), settings.embedding_stale_after_seconds,
    ).index(project_id, paper_id)


@router.get("/{paper_id}/file", response_class=Response)
def get_paper_file(project_id: str, paper_id: str, request: Request, session: Session = Depends(get_session)) -> Response:
    data = service(request, session).file(project_id, paper_id)
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": "inline"})
