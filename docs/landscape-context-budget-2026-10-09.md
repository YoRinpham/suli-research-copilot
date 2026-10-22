# Post-M6 Landscape context budget contract

This is an authorized post-M6 reliability enhancement, separate from `m6-pass` and the canonical-verification baseline `f4044d450126cb859ab8d23c6745b75323eec7b6`. It preserves exact source authorization, independent item/summary scientific verification, canonical quotation binding, human decisions, and the existing provider profile. No schema migration is required.

## Design and execution plan

1. Validate the stage-aware recovery proposal against actual generation, verification and repair builders. Initial-only sizing and independent field bounds cannot guarantee later capacity. Removing sources or raising the guard is outside this task.
2. Reserve complete scientific sources, cumulative assessments and repair critiques; reject infeasible workloads before starting a Run or contacting a provider.
3. Bound generation-only responses by Python representation size, communicate remaining allowances, and revalidate replacements. Keep historical/public schemas readable.
4. Exercise original-scope normal, structural repair, item regeneration, summary repair, combined repairs, maximum accepted responses, malformed sources, negative scientific support, persistence and fencing.
5. Audit production and protected files read-only, review the diff, create one focused commit, and evaluate production eligibility independently. Do not call a live provider when any eligibility condition fails.

## Original 7812e033 contract (historical; corrected-scope follow-up below)

All sizes use `len(str(payload))`, including escaping, dictionaries, lists, references and separator overhead. Tokens, JSON byte counts and raw string lengths are not substitutes. The existing configured guard remains 30,000 characters. The budget is local to one immutable request; there is no shared mutable service state.

Let `S` be the representation size of a summary verification context containing every input Claim (including full text, uncertainty and Evidence roles), every authorized Evidence as `{id, source_text}`, and empty summary, uncertainty, accepted-items and assessment fields. Canonical Evidence text is complete and exact. Repeated bibliographic metadata is omitted only from this verification prompt; complete chunk/paper/page metadata remains in the original source context and is bound into stored assessments. Nothing authorizes a new or omitted source ID.

The hard allowances are:

| Payload | Python representation allowance |
| --- | --- |
| Complete Landscape output (summary, uncertainty, every item and all IDs) | `O = min(12000, 30000 - S - 2500 - 1500)` |
| All accepted compact item assessments together, including server conflict roles | 2,500 |
| Summary verifier response, including qualifications, flags, references and justification | 1,500 |
| Scientific repair critique, including its field names and rejected item where applicable | 1,500 |

Preflight additionally requires at least 4,000 characters of complete-output allowance, initial sources plus 1,500 critique characters plus 24 framing characters to fit, and the worst full Claim/Evidence union for item verification plus `O` to fit. These are conservative upper bounds, not claims of scientific sufficiency for arbitrary corpora. Infeasible sources stop before provider execution. Flexible fields have a finite cumulative safe-rejection boundary even where historical response schemas retain their broader field shapes.

The fixed empty-context representation overcounts the generated field shells. Replacing its strings/lists adds no more than the corresponding complete output and assessment representations. Adding critique entries adds no more than their dictionary representation. Consequently `S + O + 2500 + 1500 <= 30000` bounds normal and repaired summary contexts. The full-source item envelope and original-source regeneration envelope are checked separately. Source subsets cannot be larger than the reserved full union; Evidence rows are deduplicated by ID as before.

Every item verifier receives an exact remaining raw-response allowance: 2,500 less prior compact assessments, the deterministic server role field, and list joining overhead. Structured repair receives the same remaining allowance. Summary repair receives its remaining complete-output allowance after retaining items. The exact transmitted instruction template and numerical policy are part of the versioned prompt bundle and hash.

Generated payloads are measured before source binding and subsequent calls. Accepted responses still pass the unchanged Pydantic and exact-source validators. Structured repair has the existing call bound. Item scientific repair regenerates the whole output, rechecks `O`, resets discarded assessments, and re-verifies every item. Summary repair replaces only summary/uncertainty, validates the complete reconstructed output against `O`, and invokes independent summary verification again. No oversized proposition or qualification is truncated, and no partially verified Landscape is persisted.

`landscape_context_budget_exceeded` (HTTP 422) reports stage, applicable allowance, measured size, current context size, actual generated contribution, repair-attempt status and `persistence_occurred=false`. Preflight separately identifies reserved/projected context and reports zero generated content. Runtime failures preserve the failed Run and actual attempt count; preflight failures create no Run. Existing log whitelisting and extra-field redaction remain intact.

Prompt version: `m3-v6`. Run/output contract version: `m3-landscape-v5`. Existing schemas for persisted/public scientific objects remain unchanged; old assessments and Runs are not rewritten.

## Original-scope capacity and scientific adequacy boundary

The exact authorized 17 Claims and 18 Evidence objects give initial input size 24,346, `S=21,522`, and `O=4,478`. This is a hard cumulative allowance, not a requested fixed item count. A deterministic three-group fixture retains every original identity and represents both commanded methods, score, endpoint/instrument qualifications, motor limitations, repeated within-run measurements, synthetic status and unresolved comparison. Grouped propositions remain linked to individual Claim/Evidence identities. Maximum-response fixtures exercise real application builders through both scientific repairs and reverification.

These fixtures are capacity tests with deterministic mock judgments, not scientific acceptance. The exact original union does not include the separate explicit shared-apparatus/session/preparation across-run dependence passage associated with the advisory scope's additional Claim. It can state that run independence is unestablished and study-level apparatus reuse/allocation/replication is unspecified; it cannot silently add that missing scientific premise. Complete representation of the task's explicit across-run dependence requirement therefore needs a separate human source-authority decision. The unapproved 13-Claim proposal is not substituted.

Production remains independently gated on scientific adequacy and a deployed committed worker with the approved runtime profile. No passing test implies provider success, persistence or human acceptance. This batch must not manufacture a Landscape acceptance packet when no Landscape exists.

## Executed engineering verification

The final deterministic backend run executed `pytest backend/tests -q -p no:cacheprovider --tb=short -k 'not real_qwen'` with database integration and live smoke disabled: **239 passed, 170 skipped, 15 deselected**. The relevant PostgreSQL run executed the M3 database, M3 semantic, M3 human-authority, canonical Landscape, cumulative-budget and M4 database modules: **126 passed, 1 deselected**. It used a newly created, migrated disposable database, deterministic providers, empty live credentials, and verified database removal. A prior broad all-module database attempt timed out after 300 seconds and is not counted as passing; the first relevant run exposed a test setup assertion that counted earlier mocked Claim calls, corrected before the passing run. No live provider was used in any test.

| Original-scope scenario | Largest submitted context |
| --- | ---: |
| Normal generation and verification | 27,958 |
| Structured authorization repair | 27,958 |
| Whole-output item repair and reverification | 27,996 |
| Summary repair and reverification | 28,033 |
| Both scientific repairs | 28,071 |
| Exact-limit output, assessments and critiques through both repairs | 29,806 |

Malformed/count/field-size responses, escaped representation growth, cumulative assessment/qualification overflow, real provider adapter with mocked HTTP, wrong-source roles, authentic canonical binding, unsupported scientific propositions, source drift, Run fencing, failed-only persistence, atomic success and legacy records are covered. The generated-output contract does not replace scientific verdicts. The independent read-only code review found no remaining actionable correctness or provenance defect after refinements.

Read-only integrity comparisons confirm all 31 production tables remain unchanged and the 37 selected Evidence, 19 supported/selected Claims, 44 pending Claims and zero revisions remain intact. The 318-file baseline differs only at the six explicitly edited existing application/test/documentation files; protected PDF, original project, benchmark assets, evaluation protocol/manifest, provider implementation/settings and original M6 annotated tag remain unchanged. There is no new scientific Run, Landscape, downstream object or acceptance; `measurement_started=false`.

Frontend display and public record shapes are unchanged, so no frontend implementation or tests were required. Production calls: **zero**. Scientific-content adequacy and committed-worker deployment readiness remain unmet; production is not yet eligible.

## Corrected-scope follow-up

The explicit human task authorizes only the original 17 Claims plus already supported/selected C60 (`fb2b9f13-d30b-4b79-9f16-e9f9f8bb1fcc`) and its actual direct selected Evidence `94ad89c6-c3ef-4f33-bf18-cdbd74159f8b`. The normal `claim_ids` request pathway needs no additional source-scope decision. The actual union has 19 Evidence objects. No human scientific object or source text is edited.

The original contract rejects this scope: initial context 25,522; original summary reservation 22,548; output allowance 3,452, below 4,000; reserved summary context 30,548. The bounded follow-up replaces repeated summary source dictionaries with reversible ID-keyed maps. Claims retain full exact text and uncertainty; `type`, `grounding`, `supports`, `challenges` carry the same original type, human grounding and supporting/challenging IDs. Evidence maps each real ID to its complete exact canonical source text. Only field names, repeated identity keys and container overhead change. Initial generation and item verification still use complete original source objects, and persisted assessments still bind complete provenance. Both summary prompts explain the aliases and independent scientific judgment remains required.

The current output allowance is `min(12000, 30000 - item_base, 30000 - S - 2500 - 1500)`, where `item_base` is the actual empty item-verification context for the full Claim/Evidence union and `S` is the actual empty summary context using the shared lossless builder. This also corrects the case where summary-safe output would exceed the separate full-source item envelope. The 4,000-character minimum, guard, assessments and critique allowances are unchanged. Oversized values fail explicitly and are never truncated. Prompt `m3-v7` and Run contract `m3-landscape-v6` distinguish this post-M6 follow-up; historical contracts and records stay readable.

For the corrected scope, `S=21,337`, `item_base=25,373`, and output allowance is 4,627 (the item envelope is limiting). Initial source contribution is 25,520 plus two representation framing characters. Conservative whole-output summary/repair envelope is 29,964; full-union item envelope is 30,000; original-source regeneration envelope is 27,046. These are character bounds, not tokens. Actual complete-workflow fixtures reach 28,021 normally, 28,059 with item repair, 28,096 with summary repair, 28,134 with both, and 29,770 with schema-valid maximum accepted output/assessments/critiques through both repairs. Structured repair reuses its guarded context and exact remaining response allowance. Accepted generated field shells are conservatively overcounted; rejection paths do not authorize unsupported content.

The three grouped fixture items preserve all 18 Claim and 19 Evidence identities, synthetic/nonempirical identity, both commanded methods, experimental unit, score, endpoint/instrument/motor restrictions, within-run repeated measurement, possible across-run apparatus/session/preparation dependence, inferential/causal limits and the corpus-bounded unresolved comparison. The one-second physical transition passage in this scope is from Method A's section; the fixture explicitly excludes complete Method B physical-conformity timing authority. A compact deterministic fixture demonstrates representability, not actual model scientific correctness or human acceptance.

Final regression and production runtime/outcome evidence for this batch is stored in `data/operator-corrected-landscape-2026-10-09/`. Original 7812e033 results and timed-out attempts above remain historical evidence, not new pass claims. Production is conditional on current clean committed code, actual HTTP health, effective approved process profile and unchanged sources. No automatic acceptance or downstream generation is authorized.

## Subsequent response-contract correction

The current generation-only contract is `m3-v8` / `m3-landscape-v7`. Request-local provider schemas enforce cumulative response budgets, and bounded repairs receive exact numeric excess feedback. The 30,000 guard, corrected-scope 4,627 allowance, source layout and scientific gates remain unchanged. See [the 2026-10-10 follow-up](landscape-response-contract-2026-10-10.md) for the recorded output failure, validator invariant and limits. Earlier version references above describe historical work.
