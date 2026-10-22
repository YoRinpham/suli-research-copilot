"""Synchronous per-paper indexing with short database transactions."""

from datetime import timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.persistence.models import Paper, PaperChunk, now_utc
from app.providers.embeddings import EMBEDDING_DIMENSIONS, EmbeddingError, EmbeddingTimeout, validate_vector
from app.providers.interfaces import EmbeddingProvider
from app.services.errors import ServiceError
from app.services.papers import PaperService


class IndexingService:
    def __init__(
        self, session: Session, papers: PaperService, provider: EmbeddingProvider,
        stale_after_seconds: int = 600,
    ) -> None:
        self.session = session
        self.papers = papers
        self.provider = provider
        self.stale_after_seconds = stale_after_seconds

    def index(self, project_id: str, paper_id: str) -> Paper:
        paper = self.papers.get(project_id, paper_id)
        if paper.parse_status == "indexed":
            chunks = list(self.session.scalars(
                select(PaperChunk).where(PaperChunk.paper_id == paper_id)
            ))
            if chunks and all(
                chunk.embedding is not None
                and len(chunk.embedding) == EMBEDDING_DIMENSIONS
                and chunk.embedding_dimension == EMBEDDING_DIMENSIONS
                and chunk.embedding_model == self.provider.model_name
                for chunk in chunks
            ):
                return paper
        if paper.parse_status in ("parse_failed", "low_quality"):
            raise ServiceError(409, "invalid_state", "Replace the unsuitable PDF before indexing", "paper_index")
        if paper.parse_status in ("uploaded", "parsing"):
            paper = self.papers.parse(paper_id)
            if paper.parse_status != "parsed":
                raise ServiceError(409, "invalid_state", "Paper parsing did not produce usable chunks", "paper_index")

        if self.provider.dimensions != EMBEDDING_DIMENSIONS:
            raise ServiceError(502, "embedding_failed", "Embedding dimension differs from database schema", "paper_index")
        claimed_at = now_utc()
        cutoff = claimed_at - timedelta(seconds=self.stale_after_seconds)
        claimed = self.session.execute(
            update(Paper).where(
                Paper.id == paper_id,
                Paper.project_id == project_id,
                or_(
                    Paper.parse_status.in_(["parsed", "embedding_failed", "indexed"]),
                    and_(Paper.parse_status == "embedding", Paper.embedding_started_at <= cutoff),
                ),
            ).values(parse_status="embedding", embedding_started_at=claimed_at, quality_note=None)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            self.session.rollback()
            raise ServiceError(409, "invalid_state", "Paper is not available for embedding", "paper_index")
        self.session.commit()

        try:
            chunks = list(self.session.scalars(
                select(PaperChunk).where(PaperChunk.paper_id == paper_id)
                .order_by(PaperChunk.page_number, PaperChunk.chunk_index)
            ))
            inputs = [(chunk.id, chunk.text) for chunk in chunks]
            self.session.rollback()  # Release the read connection before the provider call.
            if not inputs:
                raise EmbeddingError("Paper has no chunks to embed")
            vectors = self.provider.embed_documents([source for _, source in inputs])
            if not isinstance(vectors, list) or len(vectors) != len(inputs):
                raise EmbeddingError("Embedding provider returned an incomplete batch")
            vectors = [validate_vector(vector, EMBEDDING_DIMENSIONS) for vector in vectors]

            finished = self.session.execute(
                update(Paper).where(
                    Paper.id == paper_id,
                    Paper.project_id == project_id,
                    Paper.parse_status == "embedding",
                    Paper.embedding_started_at == claimed_at,
                ).values(parse_status="indexed", embedding_started_at=None, quality_note=None)
                .execution_options(synchronize_session=False)
            )
            if finished.rowcount != 1:
                raise ServiceError(409, "invalid_state", "Embedding claim was superseded", "paper_index")
            for (chunk_id, _), vector in zip(inputs, vectors, strict=True):
                result = self.session.execute(
                    update(PaperChunk).where(PaperChunk.id == chunk_id, PaperChunk.paper_id == paper_id)
                    .values(embedding=vector, embedding_model=self.provider.model_name,
                            embedding_dimension=EMBEDDING_DIMENSIONS)
                )
                if result.rowcount != 1:
                    raise EmbeddingError("Paper chunks changed during embedding")
            self.session.commit()
            paper = self.session.get(Paper, paper_id)
            self.session.refresh(paper)
            return paper
        except (EmbeddingError, EmbeddingTimeout) as exc:
            self._fail_claim(project_id, paper_id, claimed_at)
            status = 504 if isinstance(exc, EmbeddingTimeout) else 502
            code = "provider_timeout" if status == 504 else "embedding_failed"
            raise ServiceError(status, code, str(exc), "paper_index") from exc
        except ServiceError:
            self.session.rollback()
            raise
        except Exception:
            self._fail_claim(project_id, paper_id, claimed_at)
            raise

    def _fail_claim(self, project_id: str, paper_id: str, claimed_at) -> None:
        self.session.rollback()
        self.session.execute(
            update(Paper).where(
                Paper.id == paper_id, Paper.project_id == project_id,
                Paper.parse_status == "embedding", Paper.embedding_started_at == claimed_at,
            ).values(parse_status="embedding_failed", embedding_started_at=None,
                     quality_note="Embedding provider failed; retry indexing")
            .execution_options(synchronize_session=False)
        )
        self.session.commit()
