# 溯理 SULI

溯理（SULI）是以可追溯证据为基础的科研工作台，连接文献、证据、研究方向、实验与报告。

SULI is an evidence-to-experiment research workspace. M6 presents the existing, human-reviewed scientific chain as an immutable report, adds factual workflow timing, and provides a reproducible synthetic demonstration. Qwen drafts source-bound prose; stored StatisticalResults, real FigureArtifacts, provenance, and human review remain authoritative.

仓库目录、包名、数据库名、`research_copilot` 测量模式及现有 JSON 字段继续沿用原有技术标识，兼容已有环境与测量记录。

## Prerequisites

- Python 3.11 or newer, with `python` available on `PATH`
- Node.js 22.18 or newer and npm (the frontend test command runs TypeScript directly in Node)
- Docker Desktop with Docker Compose, with `docker` available on `PATH`

The commands below use PowerShell from the repository root.

## 1. Environment setup

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e './backend[dev]'
cd frontend
npm install
cd ..
```

The root `.env` is read by Docker Compose for PostgreSQL and by the backend for `DATABASE_URL` and other settings. It contains local development credentials and is ignored by Git. Set `DATABASE_URL` to match the `POSTGRES_*` variables if you change them. To index or retrieve with the production adapter, set `QWEN_API_KEY` for DashScope. Normal tests use deterministic fake embeddings and need no provider credential. `EMBEDDING_BASE_URL` is the DashScope-native text embedding endpoint and can select the appropriate Model Studio workspace and region.

Next.js reads `frontend/.env.local` when launched from `frontend/`; it does not read the root `.env`. The default backend URL is `http://localhost:8000`. To override it, run `Copy-Item frontend/.env.local.example frontend/.env.local` from the repository root and edit the copied file. Both local environment files are ignored by Git.

## 2. Start PostgreSQL

```powershell
docker compose up -d postgres
docker compose ps
```

Wait until the service is healthy. The named `postgres_data` volume keeps local data across restarts.

## 3. Run migrations

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
cd ..
```

The migrations retain the M2 `vector(1024)` space and Evidence table, add the M3 scientific objects and human review fields, M4 Gap/Hypothesis/Plan, M5 data analysis, and M6 immutable reports. Verify `0010_m6_report_delivery` with `..\.venv\Scripts\python.exe -m alembic current` while in `backend`. Historical scientific objects remain readable.

## 4. Start the backend

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000/api/v1/health`. A healthy database returns `{"status":"ok","database":"connected"}`; an unavailable database returns HTTP 503 with a structured `error` body. Both paths return `X-Request-ID` (echoing a supplied value or generating one).

## 5. Start the frontend

In a second PowerShell terminal:

```powershell
cd frontend
npm run dev
```

Open `http://localhost:3000`. Create a project, upload a PDF, and click **索引论文**. Search the indexed passages, inspect a source PDF page, then explicitly save a candidate as Evidence. Upload still returns `uploaded` and does not silently parse or index the paper. Restart the frontend after changing `frontend/.env.local`.

The frontend now uses a paper-white and botanical-green manuscript layout. Project work is grouped into 文献, 研究全景, 研究方向, 实验, and 报告, with source inspection alongside the reading column. Stage changes preserve in-session drafts and do not advance scientific decisions. See [the manuscript UI design](docs/superpowers/specs/2026-10-17-manuscript-ui-design.md) and [the demo guide](docs/demo-runbook.md).

## M1 parsing service

The public `/index` route now reuses the synchronous M1 parser for an `uploaded` paper. To exercise parsing separately, run this Python snippet from `backend`, replacing the ID with the uploaded paper ID:

```python
from sqlalchemy.orm import Session
from app.config.settings import get_settings
from app.persistence.database import get_engine
from app.services.papers import PaperService

settings = get_settings()
with Session(get_engine()) as session:
    paper = PaperService(
        session, settings.storage_root,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        min_extracted_chars=settings.min_extracted_chars,
        min_text_page_ratio=settings.min_text_page_ratio,
        max_replacement_ratio=settings.max_replacement_ratio,
        parse_stale_after_seconds=settings.parse_stale_after_seconds,
    ).parse("<paper-id>")
    print(paper.parse_status, paper.quality_note)
```

Success ends in `parsed`; unusable text ends in `low_quality`; extraction failure ends in `parse_failed`. `parsed` means chunks are persisted but no embedding or index exists. The uploaded PDF remains retrievable. A fresh `parsing` claim rejects another parse; an interrupted claim older than `PARSE_STALE_AFTER_SECONDS` (default 600) can be recovered. Recovery replaces orphan chunks in one transaction. `STORAGE_ROOT` configures the local storage directory; upload size, page count and quality thresholds are configured in `.env`.

Indexing transitions `parsed → embedding → indexed`, or `embedding → embedding_failed` on provider error. Fresh embedding rejects a second request; a claim older than `EMBEDDING_STALE_AFTER_SECONDS` can be recovered. NULL claim time is never assumed stale. Retrying `embedding_failed` reuses existing chunks. `/index` is idempotent when every Chunk already has a complete vector in the active model space. If an `indexed` Paper still has old `text-embedding-v4` vectors, `/index` claims it for re-embedding, hides it from retrieval, and atomically replaces the full vector batch before returning it to `indexed`. Failure leaves the original vectors and immutable PaperChunks in place under `embedding_failed`; a later retry can use those same chunks. Evidence text, Chunk link, page provenance and question revision are unchanged.

The active MVP model is `qwen3.7-text-embedding` with dense 1024-dimensional vectors in `vector(1024)`. Document indexing sends `text_type=document`, `dimension=1024` and `output_type=dense`. Query retrieval sends `text_type=query` with the same dimension/output type and the fixed instruction: “Given a scientific research query, retrieve relevant passages from research papers that help answer, investigate, compare, or evaluate the query.” The source of this instruction is `backend/app/providers/embeddings.py`. Retrieval uses exact pgvector cosine Top-K and compares only Chunk vectors whose stored model identity and dimension match the active provider. An old v4 vector is incompatible even though it has 1024 values. The score expresses retrieval relevance, not scientific credibility. A future dimension comparison (for example 1024 versus 2560) requires a separate benchmark and an explicit pgvector migration/reindex; this patch keeps 1024.

`UPLOAD_SIZE_LIMIT_MB` limits PDF bytes after multipart parsing. `REQUEST_BODY_LIMIT_MB` (default 30 MB) bounds the entire incoming request, including multipart overhead, at the ASGI ingress before route processing; it must exceed the PDF limit. Requests above the declared length are rejected immediately, and unknown-length requests are checked as bytes arrive. All API responses include `X-Request-ID`; ordinary database and unexpected failures return structured errors without internal details.

## 6. Run tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm test
npx tsc --noEmit
npm run build
```

The default backend suite uses deterministic synthetic PDFs and SQLite for request tests; it does not require PostgreSQL or Qwen. To run all real PostgreSQL/pgvector integration and migration checks after applying migrations, use a new terminal:

```powershell
$env:RUN_DB_INTEGRATION = '1'
cd backend
..\.venv\Scripts\python.exe -m pytest -q
```

The live checks cover indexing and model upgrades, pgvector cosine scores, bidirectional project isolation, Evidence provenance and PDF page navigation, concurrent/stale claims, and clean database migration. They use a deterministic provider. The final M2 gate also requires a separate real DashScope smoke test with synthetic text and a valid local credential; it is not part of CI.

## M3 scientific generation

Set `QWEN_API_KEY` for production generation. The scientific reasoning default is `qwen3.8-max`; an explicit `QWEN_MODEL` in the ignored local `.env` overrides it. `QWEN_BASE_URL` selects the OpenAI-compatible chat completions endpoint for the appropriate Model Studio workspace and region. Optional `QWEN_REASONING_EFFORT` accepts `xhigh`, `medium`, or `low`; when absent, the provider default is used. Restart the backend after changing it. The adapter requests strict JSON Schema output. All responses still pass JSON decoding, Pydantic validation, exact source-ID whitelisting, current Project/revision checks, and domain invariants before persistence. Provider reasoning content is ignored.

Prompts are versioned in `backend/app/prompts/scientific.py`. Source text and the research question enter as structured data and cannot change application instructions. The service validates selected inputs before creating a Run, commits `started` in a short transaction, calls Qwen without an open database session, then revalidates the exact inputs and atomically commits the scientific objects, relational source joins, and `succeeded`. A provider or validation failure leaves a `failed` Run without a partial scientific batch. `LLM_MAX_CONTEXT_CHARS` and `LLM_MAX_INPUT_ITEMS` reject oversize requests explicitly. At most one structured-output repair call uses the same Run.

In the frontend, select Evidence and explicitly request Claim generation. Review the candidate text, AI assessment, uncertainty, supporting and challenging Evidence, and source PDF pages. Explicitly decide Claim grounding and selection before requesting Research Landscape generation. Generated Landscapes start pending review; a researcher must accept one before Gap generation. Conflict items retain source links and do not declare a winner. Scientific edits create immutable new versions and require a fresh human decision. AI assessments are advisory; human support and acceptance apply only within the supplied corpus.

## M4 human-guided scientific stages

From one explicitly accepted current Landscape, request Gap candidates. Every generated Gap has `novelty_scope = supplied_literature`, direct LandscapeItem/Claim/Evidence joins, and `selection_status = candidate`. Its AI assessment may flag source or novelty concerns but cannot select it. The researcher selects or rejects a Gap before Hypothesis generation. Selection means pursuing a direction in this project, not proving global novelty.

Hypotheses originate only from a current selected Gap. Their structured proposal includes a falsification condition and distinct `motivates` / `challenges` Evidence roles. The researcher inspects the source PDFs and AI falsifiability assessment, then selects or rejects the hypothesis. Selection means choosing what to test, not declaring it true.

An ExperimentPlan originates only from a current selected Hypothesis. It includes variables, baseline, dataset requirements, metrics, procedure, analysis approach, falsification criteria, confounders, limitations, assumptions, and a separate advisory AI alignment assessment. A generated Plan starts `pending_review`; only explicit human acceptance marks it eligible for a future stage. M4 neither executes the experiment nor uploads or analyzes results.

All three stages use the existing `LLMProvider` and GenerationRun lifecycle. Static generation, assessment, and repair prompts are versioned and hashed together. Provider calls occur outside open database transactions. Final commits recheck the exact input state; failure leaves a failed Run and no partial scientific batch. Human revisions have `generation_run_id = NULL`, point to the previous version through `supersedes_id`, and reset selection or review. See `backend/evaluation/README.md` for synthetic M4 cases.

The synthetic M3 quality cases and controlled probe are described in `backend/evaluation/README.md`. They record schema/source validity and leave scientific-quality judgments for manual scoring. They are not a scientific benchmark.

## M5 experimental data and analysis

After accepting a current ExperimentPlan, use the **实验数据与结果分析** section on its project page. Upload a UTF-8 CSV with unique, nonempty headers and at least one data row. The default limits are 5 MB, 50,000 rows, and 100 columns (`CSV_SIZE_LIMIT_MB`, `CSV_ROW_LIMIT`, `CSV_COLUMN_LIMIT`); the existing request-body limit also applies. Raw CSV bytes are saved once under a UUID key in `M5_STORAGE_ROOT` and recorded with SHA-256. A cleaning run creates a separate, content-hashed DatasetVersion; the original remains downloadable. Duplicate removal, named-column whitespace trim, explicit missing-token normalization, named-column missing-row removal, and numeric/categorical casts are the only supported operations. There is no automatic outlier deletion or imputation.

Review the deterministic profile, then optionally create or request a CleaningPlan. Its ordered operation list exposes the target columns, exact missing-value tokens, and possible row deletion before human acceptance and execution. The frontend accepts and executes a Plan only when every operation has the exact currently understood parameter shape and can be fully shown for review; unknown operations, missing or mistyped parameters, and extra parameters disable approval and execution while leaving rejection available. Raw data may be selected directly for analysis when cleaning is unnecessary. Create or request an AnalysisSpec with a named method and actual CSV column mappings; human acceptance is required before deterministic computation. Supported methods are descriptive and grouped descriptive summaries, Welch independent two-group t-test, paired t-test, Pearson correlation, and Spearman correlation. Missing numeric values must be handled explicitly before analysis. Multiple executed analyses remain visible, including null or failed runs. Figures use actual data and server-owned histogram, box plot, scatter, and group summary renderers.

**LLM does not calculate statistical results. Computed statistics are produced by deterministic code from immutable dataset versions. AI interpretation is advisory until human reviewed.** Qwen receives bounded plan, hypothesis, profile, computed results, and figure metadata; complete raw rows are not sent. A categorical profile can include up to 10 category values per column, truncated to 100 characters each; these labels originate in CSV cells and are untrusted input to the model. Interpretations cite the complete relevant StatisticalResult set, undergo an independent AI caution assessment, and begin `pending_review`. Acceptance means a working interpretation for this project, never proof of the Hypothesis. Human text edits create a new pending version. M5 has no final report or report export endpoint.

M5 and M6 tests: run `..\.venv\Scripts\python.exe -m pytest -q` from `backend`; set `RUN_DB_INTEGRATION=1` for PostgreSQL provenance and migration tests. Real Qwen smoke requires configured credentials and is separate from deterministic tests.

## M6 report and competition delivery

After a current ResultInterpretation is explicitly accepted, open **最终研究报告** in the project workspace. Choose that interpretation and request a draft. The server rebuilds the full source bundle and hashes its immutable source state before calling Qwen and again before committing. The model may write short source-token-bound narrative blocks; source citations, statistical values, figures, and experiment structure are rendered from stored records. Generation failures leave a failed GenerationRun and no partial report. A report begins `pending_review`. A human can accept, reject, or create a new pending immutable revision. Final HTML and Markdown exports require a current accepted report; drafts are visibly labeled.

The report links evidence to the original PDF physical page and figures to the existing project-scoped FigureArtifact endpoint. Numbers in Qwen prose and global novelty, proof, or causal overclaims fail validation. StatisticalResult values are the sole source of authoritative report numbers. The report does not introduce a new scientific inference stage.

Run sanitized preflight from `backend` with `..\.venv\Scripts\python.exe evaluation/m6_preflight.py --network --http`; add `--provider-smoke` only for one tiny real Qwen request. The command never prints credentials or proxy values. A provider that was not live-tested is reported as degraded, while existing accepted reports remain readable. See [demo runbook](docs/demo-runbook.md), [benchmark protocol](docs/benchmark-protocol.md), and [competition submission](docs/competition-submission.md). The repository includes explicitly synthetic PDF and CSV demonstration assets under `backend/evaluation/assets`; do not present them as real scientific findings. Manual baseline timing has not been measured in this repository, so no improvement percentage is claimed.

For the controlled real-Qwen smoke check, with a valid credential and migrated PostgreSQL, run from `backend`:

```powershell
$env:RUN_DB_INTEGRATION = '1'
$env:RUN_QWEN_SMOKE = '1'
..\.venv\Scripts\python.exe -m pytest tests/test_m3_database.py::test_real_qwen38_claim_and_landscape_smoke -q
```
