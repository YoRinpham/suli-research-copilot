# M5 Data Analysis Implementation Plan

> The supplied M5 implementation specification is the approved product design for this work. Implement directly on the clean M4 PASS baseline without changing M0–M4 scientific behavior.

**Goal:** Extend an accepted current ExperimentPlan into reviewed, reproducible CSV analysis artifacts with explicit human decisions.

**Architecture:** Keep CSV parsing, cleaning, statistics, and plotting in server-owned deterministic modules. Persist immutable inputs and outputs with project and M4 lineage. Reuse GenerationRun and LLMProvider only for proposed cleaning/analysis specs and interpretations. Keep API routes thin and add one M5 project workspace component.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Alembic, Pydantic, Python CSV/NumPy/SciPy/Matplotlib, Next.js/TypeScript.

## Global Constraints

- No generated code execution and no Qwen-derived numeric results.
- Raw CSV and derived versions remain immutable, content-hashed, and project scoped.
- Every scientific decision requires explicit human review.
- No M6 report generation.

## Delivery checkpoints

- [ ] CSV parser and bounded deterministic profile; red/green tests for invalid inputs.
- [ ] Cleaning DSL and deterministic execution; red/green known-effect tests.
- [ ] Statistical method registry with method-specific validation and golden tests.
- [ ] Figure renderer with deterministic file and provenance tests.
- [ ] M5 schema and migration after 0008; populated downgrade test.
- [ ] Dataset, cleaning, analysis, figure, interpretation services and project-scoped APIs.
- [ ] Qwen proposal and interpretation prompts with bounded metadata and post-call state validation.
- [ ] Frontend M5 workflow with separate numeric results and AI narrative.
- [ ] Evaluation fixtures, documentation, full backend/PostgreSQL/frontend/migration/smoke checks.
