# Approved Landscape canonical verification implementation plan

Human approval: 2026-10-09, approving section D of data/operator-landscape-completion-route-2026-10-09/consolidated-report.md.

Goal: remove model-authored quotation transcription from new Landscape verification while preserving exact-source validation, independent scientific support critique, pending human review, and atomic persistence.

Scope: no live provider calls, production scientific writes, provider/configuration changes, migrations, evaluation asset edits, or downstream generation. Existing m6-pass tag and old records remain unchanged. Work on codex/landscape-canonical-verification, based on 031c238.

## Task 1 — versioned reference-only verification (TDD)

Files: backend/app/services/scientific_schemas.py, backend/app/prompts/scientific.py, backend/app/services/scientific.py, backend/tests/test_landscape_canonical_verification.py.

1. Write deterministic tests and observe failure on the old protocol: new schemas forbid quotations; affirmative support requires refs; duplicate/foreign/wrong-role refs fail; Unicode/newlines bind verbatim; unsupported propositions trigger bounded repair/failure.
2. Add distinct new item/summary reference-only schemas. Preserve old schema classes for historical exact-source validation. Require support verdict, flagged statements and qualifications independently of conflict/comparability.
3. Validate refs against per-item cited Claim links and support roles; bind complete canonical Evidence.source_text into legacy-shaped assessment support_excerpts. Record binding origin, version and source hashes/provenance; run the unchanged exact-substring validator on bound values.
4. Reject material unsupported/insufficient support or lost qualifications before persistence, using the existing bounded semantic repair. Summary receives verified items and source scope with independent critique.
5. Version Landscape prompt bundle/schema; retain generation, final state checks, Run fence, transactions and attempt accounting. Run new and existing unit tests.

## Task 2 — deterministic persistence and compatibility regressions

Files: backend/tests/test_landscape_observability.py and relevant M3 fixture/semantic/authority tests; new canonical database tests if needed.

1. Adapt deterministic fake providers to explicitly emit the new protocol, without production fallback for old responses.
2. Create/use an explicitly isolated test database, apply normal Alembic migrations. Never use production DATABASE_URL for tests. No real-provider tests.
3. Test pending-review canonical persistence, legacy JSON readability, unsupported item/summary failure with zero partial output, source changes/lost Run authority/commit failure, and M4 accepted-Landscape gate. Retain existing M3/M4 tests and sanitizer checks.

## Task 3 — documentation, review and integrity

Files: docs/architecture.md, docs/api-design.md, implementation verification report under data/.

1. Document reference-only provider protocol versus server-bound display text and advisory judgments; API remains compatible and human acceptance mandatory.
2. Review final diff for scope/provenance defects; run relevant deterministic tests and report actual outcomes.
3. Recheck production database canonical hashes and protected config/PDF/evaluation assets against the prior audit; confirm m6-pass unchanged and no new production Runs.
4. Deliver reviewable branch/workspace changes and test results. A future live deployment verification needs separate explicit bounded authorization.

Execution: use test-driven-development for implementation and independent subagent review as required by executing-plans; tightly coupled service/protocol work is one task. Do not infer approval of live generation or scientific acceptance.
