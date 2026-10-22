# Budget-aware Landscape generation and repair (post-M6)

Baseline: `56fa7e4fedf7e110ee1d8695b9a706b1434aa418`. This follow-up uses prompt `m3-v8` and Run contract `m3-landscape-v7`; historical/public schemas remain unchanged.

## Recorded failure and contract gap

Persisted Run `c2811541-bdc1-4c35-83ef-c7eea471e151` failed in `landscape_generation`, before independent item or summary scientific verification. The sanitized trace records an initial complete output of 5,142 Python representation characters, rejected against 4,627; one structured budget repair then produced 5,506, also rejected. Two successful HTTP attempts took 66.453 and 36.968 seconds, without transport retries or timeouts. No Landscape or Items persisted.

Both rounds already received the dynamic allowance. The provider schema still allowed an 8,000-character summary, up to 50 items, and independently large fields; cumulative validation occurred only after the provider's broad schema validation. The repair instruction stated merely that structured output was invalid, without its measured excess. The complete normalized `model_dump(mode="python")` dictionary—not JSON bytes, narrative alone, or the HTTP response envelope—was measured with `len(str(payload))`. The repair representation grew by 364 characters. Its lost model text is unavailable; neither the fields responsible nor changes in scientific meaning can be reconstructed from metadata.

## Corrected invariant

Every Landscape provider call now receives a request-local Pydantic subclass. Before and after normalization it applies the same cumulative checker as the service: whole output; summary repair merged with unchanged items; item assessments combined with prior assessments and deterministic server role; or summary assessment critique. Providers returning unvalidated dictionaries must pass the same subclass at the service boundary. Request-local fields retain existing scientific validators and cap individually impossible lengths; item count is bounded by the fixed minimum independent-assessment footprint within the existing assessment reserve. There is no required number of items. Historical/human schemas and persisted content retain their original limits.

JSON Schema does not express a cross-field Python-representation sum. Its narrower field/item constraints guide decoding; request-local validators enforce the total independently. A model may still produce invalid content. One permitted structured repair receives only numeric measured size, allowance and excess, plus instructions to regroup repeated material while retaining conditions and qualifications. Static contract and repair templates are centralized and included in the prompt digest. No generated text or source text is silently truncated or copied into diagnostic feedback.

Typed budget failures chained through provider Pydantic errors retain `landscape_context_budget_exceeded`, exact numeric diagnostics, actual transport attempt counts and sanitized stage traces. Format/source errors retain their existing bounded paths. Exact source whitelists, authentic canonical quotations, independent semantic checks, source drift rechecks, Run fencing, atomic persistence and human acceptance are unchanged.

## Exact-scope capacity

For the unchanged authorized 18 Claims and 19 Evidence (including C60): initial context 25,522; full-source item base 25,373; summary source base 21,337. The allowance remains `min(12000, 30000−25373, 30000−21337−2500−1500) = 4627`. The context guard remains 30,000, assessment reserve 2,500 and critique reserve 1,500. The approved runtime remains `qwen3.8-max / low / 120s / retry_count=2`.

The compact corrected-scope fixture uses three grouped items with all 18 Claim and 19 Evidence identities auditable. It retains synthetic/nonempirical status, task/unit, commanded A/B schedules, operational contrast, score/endpoints/instrument eligibility, commanded-versus-achieved motion, B conformity limitations, repeated-sample restrictions, across-run apparatus/session/preparation dependence, causal/generalization limitations and unresolved comparison. The across-run aggregation qualification is in the metric item with its actual supporting source. This demonstrates representational feasibility only; deterministic support mocks neither establish Qwen reliability nor replace scientific or human review.

## Verification and limits

Regressions exercise exact and one-over boundaries, the observed 5,142→5,506 class, escaped strings, cumulative multi-item metadata, remaining assessment/summary capacity, transport retries, all bounded scientific and structured repairs, exact source binding, unsupported statements, immutable history, source drift and atomic failed/successful persistence in disposable PostgreSQL. No live provider calls occur during testing. Operational test counts, database cleanup, fresh deployment readiness, the exclusive submission guard, actual production outcome and final integrity are recorded in the consolidated operator report under `data/operator-budget-aware-landscape-2026-10-10/`.

At most one normal production submission is authorized after every readiness gate passes. Failed history is preserved; a successful output remains pending human acceptance. No downstream generation or formal measurement occurs.
