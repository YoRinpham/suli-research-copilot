# Proposed Gap diagnostic contract (2026-10-12)

This is an isolated engineering proposal. It is not deployed and grants no production submission. Synthetic tests establish diagnostic and behavioral properties, not future Qwen comprehension or scientific findings. The historical Gap candidates and per-call responses were not retained and cannot be recreated by this patch.

## Scope and unchanged behavior

Only Gap execution opts into the new capture boundary. Initial generation uses `gap_generation`; every advisory uses `gap_assessment[index]`. Existing provider callbacks and bounded HTTPCore observations are reused. Sanitized default events go to `uvicorn.error`, the existing retained Worker log channel; an injected trace sink receives the same sanitized structure. No new logging framework, endpoint, database columns, configuration or dependencies are introduced.

The model, low reasoning effort, endpoint, proxy and CA policy remain unchanged. The per-attempt deadline remains 120 seconds, transport retry_count=2, context guard=30000. Gap source encoding, prompts, repair instructions, candidate quota, public GapBatch/M4Assessment schemas, validators, canonical provenance, human decisions and selection behavior are unchanged. Each generation/advisory stage still has at most two logical invocations, including its one structured repair. Ten candidates permit at most 22 logical invocations and 66 adapter request attempts, with no overall M4 deadline. The original originating-Run status fence is not repaired. Actual provider cancellation can still leave the original Run started with zero recorded attempts; tests retain this pre-existing lifecycle outcome instead of silently recovering it.

Every candidate is capacity validated before the first advisory. Events distinguish foreign Landscape items, incomplete Claim/Evidence coverage, mismatched Claim/Evidence roles, candidate assessment-context capacity, advisory source-reference validation, and source-authority drift. Existing validation predicates, raised errors and persistence behavior are retained. Diagnostic failures cannot admit or reject a candidate, add a retry, or write status.

## Capture-time allowlist

`GapEvent` is a strict extra-forbidden Pydantic schema. Project and originating Run UUIDs come from application authority. Service invocation UUIDs and adapter call UUIDs are locally generated correlation identifiers. A Run UUID is absent for failures occurring before Run creation. Stage, layer, outcome, category, validation code and remote-receipt status are fixed literals. Candidate indexes are zero-based 0–9 and repair rounds are 0–1. Counts and context sizes are bounded at 1,000,000; allowance may be negative when admission is infeasible. Timings are nonnegative bounded integers. HTTP status is 100–599. Response SHA-256 and byte length are integrity metadata, not response content.

Known Pydantic paths are `gaps[.0–9]` followed by known GapProposal scalar/list fields and bounded list indexes, or known M4Assessment fields. Arbitrary extra-field names always become `<extra_field>`; other unknown paths become `<unknown_field>`. Candidate indexes may be derived only from an already sanitized known path. No validation input, context or error message is retained.

The existing `AttemptRecord` fixed schema permits timestamps, bounded monotonic timings, call/attempt correlation, fixed transport states, at most 64 phase observations, at most four fixed numeric/enum cause records per phase, and explicit unobserved/unsupported phase lists. Gap revalidates the entire transport record through JSON; forged nested typed Pydantic instances cannot bypass literal validation. Invalid records are dropped before either logger or sink.

Forbidden content includes credentials, headers, proxy secrets, raw exception text/repr/tracebacks, source or prompt text, model response bodies, candidate prose, model-generated identifiers and arbitrary dictionary keys. No downstream redactor is relied upon. Synthetic hostile keys, nested models, validation extras and exception prose are tested at the capture boundary.

## Events and attribution

The fixed event layers are adapter, transport, provider, decode, pydantic, domain_validation, capacity, repair, provider_round, stage_outcome, persistence and capture. Outcomes are start, pass, fail, retry, scheduled, invoked, attempt_started and summary. Each event carries a monotonically bounded sequence and a snapshot of local counters.

Adapter `invoked` is the service's logical invocation; adapter `start` is entry into the actual provider adapter; adapter `attempt_started` is entry to its bounded request attempt. Transport records carry one final attempt observation and nested observed phases. Provider events distinguish pass, retry and terminal failure. Decode failures separate response JSON, envelope, incomplete response, invalid content type, missing/wrong model identity and output JSON. Non-dictionary choice entries are attributed to envelope while preserving their original AttributeError propagation and absence of structured repair. Pydantic and domain failures remain distinct. Repair scheduling and invocation round identify the existing repair, not a transport retry. Stage outcomes preserve both call-local attribution and the overall Gap outcome.

`pre_result_value_error` records the legacy fallback path before it adds one cumulative count. Capacity events carry measured size, limit, candidate allowance and index without retaining candidate text. Persistence start and the terminal stage describe existing execution; they do not replace canonical Run reconciliation.

## Accounting and limitations

- `logical_invocations`: service entries into a generation/advisory provider round, including repair rounds.
- `adapter_invocations`: observed entries into DashScope generate_structured, before request construction. A logical invocation may fail before this observation.
- `adapter_request_attempts`: observed adapter entries into the actual `_post` attempt path. It does not mean the request reached the network or remote server.
- `transport_records`: sanitized final transport-attempt records captured. Nested `events` are observations of supported HTTPCore phases, not extra calls. DNS, pool wait and socket close are explicitly unsupported; unobserved phases remain unknown.
- `retry_decisions`: provider decisions to retry a transport/request failure. These are distinct from `structured_repairs`, counted when the next structured round is invoked.
- `legacy_cumulative_attempts`: unchanged service/Run cumulative count. A pre-result ValueError or generic exception can add one without an adapter request; this is never relabeled as exact network traffic.

`capture_complete` means the observed logical/adapter/request/final-record accounting matches and no invalid, dropped or delivery-failed event is known. Intermediate start events can be incomplete while an attempt is pending. It does not assert full phase coverage, durable log delivery, remote receipt, or authentication acceptance. `remote_authenticated_receipt` is always `unknown` in this contract. Even an HTTP status or completed send is not a credential-acceptance receipt.

At most 512 regular events plus one reserved terminal summary are emitted per Gap execution. Each bounded transport record separately discloses dropped/ignored phase events. Invalid, dropped and sink-failure counters mark capture incomplete. No exception payload or event list is retained by the collector. ContextVar binding is reset on every exit and suspended during unrelated nested stages. Concurrent Run isolation is tested.

Logger/sink failures and diagnostic bookkeeping exceptions or cancellation are swallowed and mark capture incomplete where possible; actual provider cancellation is re-raised unchanged. If the only output channel fails for the entire operation, even its summary may be unavailable. No secondary logging side effect, retry or persistence is introduced to compensate. Read-only originating-Run reconciliation remains necessary after ambiguous responses.

## Synthetic examples and verification

The review package contains sanitized synthetic engineering event examples for connection exhaustion, malicious extra-field schema failures and pre-result ValueError accounting. They are not historical responses, scientific candidates or remote receipts. Fresh test results, import identities, external-access fences, disposable-database creation/cleanup receipts, exact selected/deselected cases and byte reconstruction proof are retained alongside the patch.

Any later deployment requires review of the exact patch SHA and dirty baseline identities, separate authorization for serving changes/restart, loaded-module/log-channel verification and protected-state checks. A subsequent controlled-window production submission needs a fresh direct single-POST grant. Current allowance remains zero.
