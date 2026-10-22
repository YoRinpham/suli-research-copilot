# M3 synthetic quality evaluation

`m3_cases.json` is a synthetic probe set, not a scientific benchmark. It covers direct support, comparison, limitations, association versus causation, absent significance testing, unsupported comparison, conditional findings, comparable conflicting results, different datasets, metric mismatch, different baselines, and instruction-like source text.

From `backend`, with a configured `QWEN_API_KEY`, run:

```powershell
..\.venv\Scripts\python.exe -m evaluation.m3_quality --output ..\data\m3-quality-results.json
```

The lightweight probe uses `qwen3.8-max` explicitly, validates returned schemas and source IDs, and writes scenario ID, model, prompt versions and complete bundle hashes, provider attempt count, generated Claim count, and latency. It does not create a GenerationRun, so that ID remains null. Pass `--case-id` to limit a run. Its record has separate generator proposal, AI verifier assessment, human scientific judgment, and final downstream eligibility fields. The lightweight probe does not run the verifier or human review; those fields stay null. The `human_evaluation` labels are deliberately blank: manually selected/rejected counts, GROUNDED, GROUNDED_WITH_CAVEAT, OVERCLAIM, UNSUPPORTED, TRUE_CONFLICT, CONDITIONAL_DIFFERENCE, FALSE_CONFLICT, and UNCLEAR require a human assessor. They are never inferred from schema validation. These fields permit later measurement of verifier agreement, overclaim precision/recall, human Claim acceptance and override rates, conflict agreement, and Landscape acceptance, once enough human labels exist. No such metrics are claimed now.

For a focused end-to-end Qwen check through the persistence boundary, from `backend` run:

```powershell
$env:RUN_DB_INTEGRATION='1'; $env:RUN_REAL_QWEN='1'; ..\.venv\Scripts\python.exe -m pytest tests/test_m3_real_qwen.py -q -s
```

This creates only synthetic, temporary Project/Paper/Evidence records and writes generator proposals, AI semantic assessments, human review state before any test action, final downstream eligibility, prompt bundle hash, GenerationRun ID, and attempt count to ignored `data/m3-live-semantic-results.json`. Landscape tests use explicit deterministic synthetic review actions for fixture Claims so they can exercise the stage; those actions are not real researcher evaluation. The test fixture removes its database records. It requires configured Qwen credentials and a migrated local PostgreSQL. Raw outputs can be regenerated with the same command; they are not tracked. Inspect each record before assigning human labels. A small live sample cannot establish general model accuracy.

The application `GenerationRun` records successful and failed production operations with model, prompt/schema version, input lineage, all provider attempts, selection state via Claims, and timestamps. The full static prompt bundle hash changes when generation, repair, or verifier instructions change. Semantic verification adds inference latency and cost; measure both in the later competition benchmark.

## M4 synthetic cases and benchmark fields

`m4_cases.json` covers corpus-limited gaps, false global novelty, limited corpora, unresolved conflict, unfalsifiable and restated hypotheses, unsupported precision, condition loss, plan metric/comparator/context mismatch, inability to falsify, an aligned plan, and instruction-like source text. `m4_quality.evaluation_record` creates stage-specific records with generator proposal, source validity, AI assessment, human decision, and final eligibility kept separate. Human labels start blank; the case's expected probe label is a regression target and never treated as an observed scientific judgment.

For reproducible workflow timing and counts, use `GenerationRun.created_at`, `completed_at`, `attempt_count`, `model`, `prompt_version`, and `prompt_hash` together with the scientific object's `created_at`, selection/review state and `reviewed_at` where available. The source Landscape ID, selected Gap ID, selected Hypothesis ID, and accepted Plan ID form the lineage for each project. Rates should be computed from explicit human states, not AI assessment verdicts. This is instrumentation only; M4 has no analytics dashboard.

## M5 synthetic data, correctness, and interpretation evaluation

`m5_cases.json` identifies deterministic cleaning and numerical fixtures plus interpretation traps: null-result overclaim, p-value fallacy, observational causality, numeric mutation, and instruction-like CSV metadata. `m5_quality.evaluation_record` keeps proposal, server computation, model assessment, human review, and scientific-quality label separate. Human labels deliberately remain blank. Numerical correctness is checked against independently known small datasets in `tests/test_m5_deterministic.py`; the PostgreSQL suite checks raw immutability, complete result retention, review gates, figure files, and the PDF-to-interpretation chain.

For timing, pair `Dataset.created_at`, `CleaningRun.started_at/completed_at`, `AnalysisRun.started_at/completed_at`, `VisualizationSpec.created_at/completed_at`, and `GenerationRun.created_at/completed_at` with human `reviewed_at` fields. These timestamps cannot reconstruct precise upload-to-profile latency because parsing and profiling happen before the Dataset record is created; capture that interval separately in an M6 benchmark if needed. `m5_manual_baseline.csv` is an intentionally unfilled worksheet for the same synthetic two-group scenario. No efficiency percentage is claimed until a researcher records actual manual and assisted timings. M6 may compare those observations; M5 supplies only the scaffold. Qwen receives profile metadata and deterministic results, never complete raw dataset rows by default; categorical profile labels can contain up to 10 cell-derived values per column, each truncated to 100 characters. The opt-in `tests/test_m5_real_qwen.py::test_real_qwen_m5_cleaning_proposal_only` smoke makes one bounded proposal call with synthetic rows and never approves a plan; run it from `backend` with `RUN_DB_INTEGRATION=1` and `RUN_REAL_QWEN=1`. The separate full-path smoke writes an ignored `data/m5-live-results.json` only on success; its human acceptance actions are explicitly labeled as test actions.

## M6 report and competition evaluation

`m6_scenario.json` and `assets/` contain fixed, SHA-256-bound synthetic papers and CSV. `m6_benchmark.py` records actual stage intervals and human scientific decisions; SULI mode also derives provider/deterministic run counts from persisted timestamps. `m6_preflight.py` emits sanitized readiness categories and optionally executes one tiny live provider probe. Generated measurement files are ignored by Git. [Benchmark protocol](../../docs/benchmark-protocol.md) defines same-scenario comparison and `NOT MEASURED` output when the manual baseline is missing.

The product is named **溯理 SULI**. Existing CLI flags, JSON keys, the `research_copilot` mode and measurement filenames remain compatible. Existing synthetic PDF assets retain their original bytes and metadata so their scenario hashes remain valid; newly generated assets use the SULI author label.

Automated M6 tests check source-token whitelisting, numeric/proof/novelty/causal text rejection, deterministic export, migration, stale-source failure and full PDF-page/StatisticalResult/FigureArtifact provenance where PostgreSQL is available. A researcher must separately rate report prose as `SOURCE_FAITHFUL`, `NUMERICALLY_FAITHFUL`, `CAUTIOUS`, `OVERCLAIM`, `MISSING_LIMITATION`, `UNSOURCED_ASSERTION`, or `NOVELTY_OVERCLAIM`; automated validation cannot supply these human labels. The current repository contains no real human report-quality scores and no measured manual workflow baseline.
