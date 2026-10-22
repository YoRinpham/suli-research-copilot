# Budget-aware Landscape generation and repair implementation plan

**Goal:** Enforce the existing request-local cumulative output allowance at provider schema validation and give bounded repairs precise numeric feedback.

**Architecture:** Keep historical/public schemas unchanged. Build request-local subclasses in `LandscapeBudget`, preserving schema names and scientific invariants. Validate complete Python representations before normalization and afterward. Extract only our typed budget exception from the provider's existing chained Pydantic error; retain transport attempt counts and the specific sanitized budget failure. Reuse the same contract for generation, structured repair, semantic regeneration, item/summary assessments and summary repair.

**Constraints:** 30,000-character context guard; actual approved output allowance 4,627; assessment reserve 2,500; critique reserve 1,500; qwen3.8-max / low / 120s / retry_count=2. No truncation, authority changes, source loss, tag changes or live provider calls in tests. One focused commit and at most one conditional normal production POST.

- [ ] Reconcile latest persisted Run and sanitized trace; snapshot protected assets and authority using read-only transactions.
- [ ] Add failing tests in `backend/tests/test_landscape_response_contract.py`: exact/one-over boundaries, observed 5142→5506 repair failure, numeric repair feedback, real adapter with mocked transport, summary merge and escaped-string accounting.
- [ ] Run `python -m pytest tests/test_landscape_response_contract.py -q -p no:cacheprovider`; confirm missing response schema/feedback failures.
- [ ] Implement request-local schemas in `backend/app/services/landscape_budget.py`; retain field limits, dynamically cap individually impossible sizes and item counts, and validate cumulative output at the boundary. Add contract prose to the versioned prompt bundle.
- [ ] Wire schemas and sanitized repair feedback in `backend/app/services/scientific.py`; recover typed budget exceptions from chained Pydantic failures without storing private response text.
- [ ] Rerun new tests and existing Landscape budgets/canonical/observability suites. Update the one mock provider that compares schema class identity to use subclass compatibility.
- [ ] Run relevant PostgreSQL regressions in a new disposable database with real migrations, no provider credentials; verify removal afterward.
- [ ] Review exact 18-Claim/19-Evidence compact fixture against sources, especially B conformity and across-run dependence; record representational feasibility separately from scientific AI acceptance.
- [ ] Review diff, document invariant and limitations, verify protected files/tag/database authority, commit only verified change.
- [ ] Start a committed worker with the approved profile; independently check listener/process ownership and health; submit once only if every readiness gate passes. Reconcile read-only if ambiguous.
- [ ] Save consolidated report; if successful save full Markdown human acceptance packet; keep pending review and measurement_started=false.

The explicit task already authorizes this narrow design and inline execution. No extra design commit, separate worktree, provider experiments or downstream generation is required.
