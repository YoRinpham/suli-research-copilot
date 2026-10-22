# Corrected Landscape scope budget follow-up

User authorization: complete the integrated batch and apply a narrowly necessary post-M6 cumulative-budget adjustment without another routine approval gate. This plan is executed inline in the existing clean checkout used for production verification.

## Proven need and design

The authoritative 17 Claim manifest plus C60 produces 18 Claims and 19 actual Evidence IDs. Existing sources reserve 22,548 summary characters and allow only 3,452 output characters; the 4,000-character minimum rejects at 30,548. Raising the guard, removing scientific sources, or reducing scientific allowances is outside scope.

Use a reversible summary-only representation: `claims` is keyed by original Claim ID, retaining text/uncertainty and the same type, human grounding, support/challenge IDs under `type`, `grounding`, `supports`, `challenges`; `evidence` maps original Evidence ID to complete exact source text. The provider prompts explain this layout. Item verification and generation retain original complete source contexts. Canonical server binding/persistence stays unchanged. Calculate output allowance as the minimum of the summary reservation, full-union item verification allowance, and existing maximum. No truncation or broader scientific authority.

Alternatives considered: keep the existing rejection (safe but prevents authorized production); lower scientific/repair reservations (unjustified); lossless representation plus actual stage envelopes (chosen).

## Execution checklist

- [x] Add corrected real synthetic source fixture and tests that reconstruct every field, reject wrong source/negative judgments, and exercise normal/structural/item/summary/combined/max-limit workflows. Run before implementation and retain expected failure evidence.
- [x] Add one summary-source builder reused by budget and actual summary contexts; use dynamic minimum across item and summary envelopes. Version the modified Landscape prompt/schema contract only. Preserve Claim and historical contracts.
- [x] Verify current and corrected scopes, all relevant deterministic regressions, and migrated isolated PostgreSQL regressions with live credentials removed. Record exact counts and stage sizes.
- [ ] Review diff, documentation and full protected-file/database integrity. Commit only this follow-up.
- [ ] Start a fresh hidden normal worker at the exact clean commit with the already approved process-level low deployment setting; record loaded module hashes, actual health HTTP and provider profile without a provider call.
- [ ] Submit exactly one normal production POST only if all named checks pass; use exclusive marker before submission. On ambiguous response reconcile read-only. Never repeat POST, auto-accept, or generate downstream objects.
- [ ] Audit atomic outcome and unchanged human decisions/history/assets; write consolidated Markdown report and a full acceptance packet only if actual Landscape persistence succeeds.

Engineering checkpoint: 249 deterministic passed, 172 skipped, 15 deselected; 128 isolated PostgreSQL passed, 1 deselected. No final failures/timeouts. Review found no actionable issue. All six corrected-scope workflows passed; maximum measured context 29,770. Initial intentional red tests failed at the old budget, and follow-up positive/negative tests pass. Production phases remain conditional.
