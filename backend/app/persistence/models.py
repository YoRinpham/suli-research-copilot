from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import VECTOR

from app.embedding_space import EMBEDDING_DIMENSIONS

from app.persistence.base import Base


def new_id() -> str:
    return str(uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    papers: Mapped[list["Paper"]] = relationship(back_populates="project")

    __table_args__ = (
        CheckConstraint("question_revision >= 1", name="ck_projects_revision_positive"),
        CheckConstraint("length(trim(title)) > 0", name="ck_projects_title_nonempty"),
        CheckConstraint("length(trim(research_question)) > 0", name="ck_projects_question_nonempty"),
    )


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quality_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    project: Mapped[Project] = relationship(back_populates="papers")
    chunks: Mapped[list["PaperChunk"]] = relationship(back_populates="paper", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("page_count IS NULL OR page_count > 0", name="ck_papers_page_count_positive"),
        CheckConstraint(
            "parse_status IN ('uploaded','parsing','parsed','embedding','parse_failed','low_quality','embedding_failed','indexed')",
            name="ck_papers_parse_status",
        ),
    )


class PaperChunk(Base):
    __tablename__ = "paper_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(EMBEDDING_DIMENSIONS), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper: Mapped[Paper] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("paper_id", "page_number", "chunk_index", name="uq_chunk_page_order"),
        CheckConstraint("page_number >= 1", name="ck_chunk_page_positive"),
        CheckConstraint("chunk_index >= 0", name="ck_chunk_index_nonnegative"),
        CheckConstraint("length(trim(text)) > 0", name="ck_chunk_text_nonempty"),
        CheckConstraint(
            "(embedding IS NULL AND embedding_model IS NULL AND embedding_dimension IS NULL) OR "
            "(embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_dimension = 1024)",
            name="ck_chunk_embedding_complete",
        ),
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    paper_chunk_id: Mapped[str] = mapped_column(ForeignKey("paper_chunks.id"), nullable=False, index=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    relevance_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="selected")
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    chunk: Mapped[PaperChunk] = relationship()

    __table_args__ = (
        CheckConstraint("length(trim(source_text)) > 0", name="ck_evidence_source_nonempty"),
        CheckConstraint("question_revision >= 1", name="ck_evidence_revision_positive"),
        CheckConstraint("selection_status IN ('selected','rejected')", name="ck_evidence_selection"),
        CheckConstraint(
            "evidence_type IN ('reported_result','method_description','dataset_detail','limitation','author_claim','other')",
            name="ck_evidence_type",
        ),
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    research_question_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_manifest: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("stage IN ('claim','landscape','gap','hypothesis','experiment_plan','cleaning_plan','analysis_spec','result_interpretation','research_report')", name="ck_generation_run_stage"),
        CheckConstraint("status IN ('started','succeeded','failed')", name="ck_generation_run_status"),
        CheckConstraint("attempt_count >= 0", name="ck_generation_run_attempts"),
        CheckConstraint("question_revision >= 1", name="ck_generation_run_revision"),
    )


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(24), nullable=False)
    legacy_grounding_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_grounding_assessment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_risk_flags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    ai_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_evidence_refs: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    ai_support_excerpts: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB, nullable=True)
    human_grounding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    human_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    selection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    evidence_links: Mapped[list["ClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(trim(text)) > 0 AND length(trim(uncertainty)) > 0", name="ck_claim_scientific_text"),
        CheckConstraint("claim_type IN ('finding','method','comparison','limitation','other')", name="ck_claim_type"),
        CheckConstraint("legacy_grounding_status IS NULL OR legacy_grounding_status IN ('supported','inferred','unsupported','insufficient_evidence')", name="ck_claim_legacy_grounding"),
        CheckConstraint("ai_grounding_assessment IS NULL OR ai_grounding_assessment IN ('directly_supported','inferentially_supported','unsupported','contradicted','insufficient_evidence')", name="ck_claim_ai_grounding"),
        CheckConstraint("human_grounding_status IN ('pending_review','supported','inferred','unsupported','insufficient_evidence')", name="ck_claim_human_grounding"),
        CheckConstraint("confidence IN ('low','medium','high','unknown')", name="ck_claim_confidence"),
        CheckConstraint("selection_status IN ('candidate','selected','rejected')", name="ck_claim_selection"),
        CheckConstraint("question_revision >= 1", name="ck_claim_revision"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR "
                        "(origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)",
                        name="ck_claim_origin_lineage"),
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    claim: Mapped[Claim] = relationship(back_populates="evidence_links")

    __table_args__ = (CheckConstraint("role IN ('supports','challenges')", name="ck_claim_evidence_role"),)


class ResearchLandscape(Base):
    __tablename__ = "research_landscapes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_summary_assessments: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("research_landscapes.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    items: Mapped[list["LandscapeItem"]] = relationship(back_populates="landscape", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(trim(summary)) > 0 AND length(trim(uncertainty)) > 0", name="ck_landscape_scientific_text"),
        CheckConstraint("question_revision >= 1", name="ck_landscape_revision"),
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_landscape_review"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR "
                        "(origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)",
                        name="ck_landscape_origin_lineage"),
    )


class LandscapeItem(Base):
    __tablename__ = "landscape_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    landscape_id: Mapped[str] = mapped_column(ForeignKey("research_landscapes.id"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    ai_conflict_assessment: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    landscape: Mapped[ResearchLandscape] = relationship(back_populates="items")
    claim_links: Mapped[list["LandscapeItemClaim"]] = relationship(back_populates="item", cascade="all, delete-orphan")
    evidence_links: Mapped[list["LandscapeItemEvidence"]] = relationship(back_populates="item", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("category IN ('approach','method','dataset','metric','finding','conflict')", name="ck_landscape_item_category"),
        CheckConstraint("length(trim(text)) > 0 AND length(trim(uncertainty)) > 0", name="ck_landscape_item_text"),
    )


class LandscapeItemClaim(Base):
    __tablename__ = "landscape_item_claims"

    item_id: Mapped[str] = mapped_column(ForeignKey("landscape_items.id"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    item: Mapped[LandscapeItem] = relationship(back_populates="claim_links")


class LandscapeItemEvidence(Base):
    __tablename__ = "landscape_item_evidence"

    item_id: Mapped[str] = mapped_column(ForeignKey("landscape_items.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    item: Mapped[LandscapeItem] = relationship(back_populates="evidence_links")


class ResearchGap(Base):
    __tablename__ = "research_gaps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    landscape_id: Mapped[str] = mapped_column(ForeignKey("research_landscapes.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    gap_type: Mapped[str] = mapped_column(String(40), nullable=False)
    why_it_matters: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    novelty_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="supplied_literature")
    ai_assessment: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    selection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("research_gaps.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    item_links: Mapped[list["GapLandscapeItem"]] = relationship(cascade="all, delete-orphan")
    claim_links: Mapped[list["GapClaim"]] = relationship(cascade="all, delete-orphan")
    evidence_links: Mapped[list["GapEvidence"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0 AND length(trim(description)) > 0 AND length(trim(why_it_matters)) > 0 AND length(trim(uncertainty)) > 0", name="ck_gap_scientific_text"),
        CheckConstraint("gap_type IN ('contradictory_findings','underexplored_variable','missing_comparison','limited_dataset','insufficient_replication','methodological_limitation','untested_interaction','unexplored_domain','unresolved_cause','evaluation_inconsistency')", name="ck_gap_type"),
        CheckConstraint("novelty_scope = 'supplied_literature'", name="ck_gap_corpus_scope"),
        CheckConstraint("selection_status IN ('candidate','selected','rejected')", name="ck_gap_selection"),
        CheckConstraint("question_revision >= 1", name="ck_gap_revision"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)", name="ck_gap_origin_lineage"),
    )


class GapLandscapeItem(Base):
    __tablename__ = "gap_landscape_items"
    gap_id: Mapped[str] = mapped_column(ForeignKey("research_gaps.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("landscape_items.id"), primary_key=True)


class GapClaim(Base):
    __tablename__ = "gap_claims"
    gap_id: Mapped[str] = mapped_column(ForeignKey("research_gaps.id"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)


class GapEvidence(Base):
    __tablename__ = "gap_evidence"
    gap_id: Mapped[str] = mapped_column(ForeignKey("research_gaps.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('supports','challenges')", name="ck_gap_evidence_role"),)


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    research_gap_id: Mapped[str] = mapped_column(ForeignKey("research_gaps.id"), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    independent_variable: Mapped[str] = mapped_column(Text, nullable=False)
    dependent_variable: Mapped[str] = mapped_column(Text, nullable=False)
    control_variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    falsification_condition: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    ai_assessment: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    selection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("hypotheses.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    evidence_links: Mapped[list["HypothesisEvidence"]] = relationship(cascade="all, delete-orphan")
    claim_links: Mapped[list["HypothesisClaim"]] = relationship(cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(trim(statement)) > 0 AND length(trim(rationale)) > 0 AND length(trim(independent_variable)) > 0 AND length(trim(dependent_variable)) > 0 AND length(trim(falsification_condition)) > 0 AND length(trim(uncertainty)) > 0", name="ck_hypothesis_scientific_text"),
        CheckConstraint("selection_status IN ('candidate','selected','rejected')", name="ck_hypothesis_selection"),
        CheckConstraint("question_revision >= 1", name="ck_hypothesis_revision"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)", name="ck_hypothesis_origin_lineage"),
    )


class HypothesisEvidence(Base):
    __tablename__ = "hypothesis_evidence"
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('motivates','challenges')", name="ck_hypothesis_evidence_role"),)


class HypothesisClaim(Base):
    __tablename__ = "hypothesis_claims"
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="extends")
    __table_args__ = (CheckConstraint("role IN ('challenges','extends')", name="ck_hypothesis_claim_role"),)


class ExperimentPlan(Base):
    __tablename__ = "experiment_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), nullable=False, index=True)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    independent_variable: Mapped[str] = mapped_column(Text, nullable=False)
    dependent_variable: Mapped[str] = mapped_column(Text, nullable=False)
    control_variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    experimental_groups: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    baseline: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_requirements: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    procedure: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_approach: Mapped[str] = mapped_column(Text, nullable=False)
    expected_observations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    falsification_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    confounders: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    ai_alignment_assessment: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("experiment_plans.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    __table_args__ = (
        CheckConstraint("length(trim(objective)) > 0 AND length(trim(baseline)) > 0 AND length(trim(falsification_criteria)) > 0", name="ck_plan_scientific_text"),
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_plan_review"),
        CheckConstraint("question_revision >= 1", name="ck_plan_revision"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)", name="ck_plan_origin_lineage"),
    )


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    experiment_plan_id: Mapped[str] = mapped_column(ForeignKey("experiment_plans.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (CheckConstraint("byte_size > 0", name="ck_dataset_size"),)


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"), nullable=False, index=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.id"), nullable=True)
    cleaning_run_id: Mapped[str | None] = mapped_column(ForeignKey("cleaning_runs.id", ondelete="CASCADE"), nullable=True, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        CheckConstraint("kind IN ('raw','cleaned')", name="ck_dataset_version_kind"),
        CheckConstraint("row_count > 0 AND column_count > 0", name="ck_dataset_version_shape"),
        CheckConstraint("(kind = 'raw' AND parent_version_id IS NULL AND cleaning_run_id IS NULL) OR (kind = 'cleaned' AND parent_version_id IS NOT NULL AND cleaning_run_id IS NOT NULL)", name="ck_dataset_version_lineage"),
    )


class CleaningPlan(Base):
    __tablename__ = "cleaning_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    experiment_plan_id: Mapped[str] = mapped_column(ForeignKey("experiment_plans.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    operations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_cleaning_plan_review"),
        CheckConstraint("origin IN ('human','model')", name="ck_cleaning_plan_origin"),
    )


class CleaningRun(Base):
    __tablename__ = "cleaning_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    cleaning_plan_id: Mapped[str] = mapped_column(ForeignKey("cleaning_plans.id"), nullable=False)
    source_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rows_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    columns_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effects: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("status IN ('started','succeeded','failed')", name="ck_cleaning_run_status"),)


class AnalysisSpec(Base):
    __tablename__ = "analysis_specs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    experiment_plan_id: Mapped[str] = mapped_column(ForeignKey("experiment_plans.id"), nullable=False)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    column_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_analysis_spec_review"),
        CheckConstraint("origin IN ('human','model')", name="ck_analysis_spec_origin"),
    )


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    analysis_spec_id: Mapped[str] = mapped_column(ForeignKey("analysis_specs.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    column_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("status IN ('started','succeeded','failed')", name="ck_analysis_run_status"),)


class StatisticalResult(Base):
    __tablename__ = "statistical_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(48), nullable=False)
    values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class VisualizationSpec(Base):
    __tablename__ = "visualization_specs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    statistical_result_id: Mapped[str | None] = mapped_column(ForeignKey("statistical_results.id"), nullable=True)
    chart_type: Mapped[str] = mapped_column(String(32), nullable=False)
    x_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    y_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    group_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    options: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("chart_type IN ('histogram','box_plot','scatter','group_summary')", name="ck_visualization_chart_type"),
                      CheckConstraint("status IN ('started','succeeded','failed')", name="ck_visualization_status"))


class FigureArtifact(Base):
    __tablename__ = "figure_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    visualization_spec_id: Mapped[str] = mapped_column(ForeignKey("visualization_specs.id"), nullable=False)
    dataset_version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id"), nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id"), nullable=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    renderer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResultInterpretation(Base):
    __tablename__ = "result_interpretations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis_relationship: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    alternative_explanations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    statistical_result_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    figure_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    ai_assessment: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("result_interpretations.id"), nullable=True, unique=True)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_interpretation_review"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR (origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)", name="ck_interpretation_origin"),
    )


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    question_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    generation_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"), nullable=True)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_review")
    source_bundle: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    narrative_blocks: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("research_reports.id"), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("review_status IN ('pending_review','accepted','rejected')", name="ck_report_review"),
        CheckConstraint("(origin = 'model' AND generation_run_id IS NOT NULL AND supersedes_id IS NULL) OR "
                        "(origin = 'human_revision' AND generation_run_id IS NULL AND supersedes_id IS NOT NULL)",
                        name="ck_report_origin"),
        CheckConstraint("question_revision >= 1", name="ck_report_revision"),
    )
