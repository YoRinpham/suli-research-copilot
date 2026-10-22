"""Project-scoped exact pgvector retrieval; candidates remain transient."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import Paper, PaperChunk, Project
from app.providers.embeddings import EMBEDDING_DIMENSIONS, EmbeddingError, EmbeddingTimeout, validate_vector
from app.providers.interfaces import EmbeddingProvider
from app.services.errors import ServiceError


class RetrievalService:
    def __init__(self, session: Session, provider: EmbeddingProvider, default_top_k: int, max_top_k: int) -> None:
        self.session = session
        self.provider = provider
        self.default_top_k = default_top_k
        self.max_top_k = max_top_k

    def retrieve(
        self, project_id: str, query: str | None, top_k: int | None, revision: int,
    ) -> dict[str, object]:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ServiceError(404, "not_found", "Project not found", "retrieval")
        if project.question_revision != revision:
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "retrieval")
        limit = self.default_top_k if top_k is None else top_k
        if limit < 1 or limit > self.max_top_k:
            raise ServiceError(422, "validation_error", "top_k exceeds the configured limit", "retrieval")
        effective_query = query.strip() if query and query.strip() else project.research_question
        if self.provider.dimensions != EMBEDDING_DIMENSIONS:
            raise ServiceError(502, "embedding_failed", "Embedding dimension differs from database schema", "retrieval")
        eligible = self.session.scalar(
            select(PaperChunk.id).join(Paper).where(
                Paper.project_id == project_id, Paper.parse_status == "indexed",
                PaperChunk.embedding.is_not(None),
                PaperChunk.embedding_model == self.provider.model_name,
                PaperChunk.embedding_dimension == EMBEDDING_DIMENSIONS,
            ).limit(1)
        )
        self.session.rollback()
        if eligible is None:
            raise ServiceError(424, "no_relevant_chunks", "No searchable chunks are available", "retrieval")
        try:
            vector = validate_vector(self.provider.embed_query(effective_query), EMBEDDING_DIMENSIONS)
        except EmbeddingTimeout as exc:
            raise ServiceError(504, "provider_timeout", "Embedding provider timed out", "retrieval") from exc
        except EmbeddingError as exc:
            raise ServiceError(502, "embedding_failed", "Embedding provider failed", "retrieval") from exc

        current_revision = self.session.scalar(
            select(Project.question_revision).where(Project.id == project_id).with_for_update()
        )
        if current_revision != revision:
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "retrieval")
        distance = PaperChunk.embedding.cosine_distance(vector)
        rows = self.session.execute(
            select(PaperChunk, Paper, distance.label("distance"))
            .join(Paper, PaperChunk.paper_id == Paper.id)
            .where(
                Paper.project_id == project_id, Paper.parse_status == "indexed",
                PaperChunk.embedding.is_not(None),
                PaperChunk.embedding_model == self.provider.model_name,
                PaperChunk.embedding_dimension == EMBEDDING_DIMENSIONS,
            )
            .order_by(distance, PaperChunk.id)
            .limit(limit)
        ).all()
        if not rows:
            raise ServiceError(424, "no_relevant_chunks", "No searchable chunks are available", "retrieval")
        return {"query": effective_query, "results": [
            {
                "chunk_id": chunk.id, "paper_id": paper.id, "paper_title": paper.title,
                "page_number": chunk.page_number, "section": chunk.section, "text": chunk.text,
                "similarity_score": 1.0 - float(score),
            } for chunk, paper, score in rows
        ]}
