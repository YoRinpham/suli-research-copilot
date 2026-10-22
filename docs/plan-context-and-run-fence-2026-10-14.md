# Plan provider representation and originating-Run persistence

This proposed patch is isolated and not deployed. Public requests, PlanProposal, persisted schema, dependencies, provider settings and the 30,000-character guard remain unchanged.

## Provider authority and capacity

Plan uses `plan-context-v1`: exact question; complete selected Hypothesis scientific fields and source arrays; complete necessary selected Gap proposal; inherited Hypothesis Claim/Evidence rows with full retained prose, qualifications, canonical paper/chunk/physical-page provenance; relevant Landscape/item metadata; distinct canonical Hypothesis and Gap relationship roles. The full Landscape narrative and source rows outside the Hypothesis's inherited set are omitted from provider representation. This is representation filtering, not a source or human scientific revision. No retained scientific prose is shortened.

The original authoritative `info`, validation sets and manifest remain separate from the pure copied provider payload. Before-Run and final question/currentness/selection/provenance/manifest checks remain. For Plan advisory only, visible canonical domain-row IDs intersect the existing authoritative allowed set. An excluded Claim/Evidence mentioned in complete Gap reference arrays is not a supplied advisory source.

The existing manifest covers Claim text/type/uncertainty, selection/review metadata and Evidence relationships; Evidence source text and chunk/paper/page bindings; Landscape summary/uncertainty, review and item text/references; complete Gap/Hypothesis proposal dictionaries and selection. It does not comprehensively hash all provider metadata: Paper title, item category/uncertainty, explicit HypothesisClaim role, full Chunk text, PDF bytes/storage key and Evidence selection are not all represented in this stage's manifest. This patch preserves that coverage; it adds no broader manifest guarantee or hidden source-fencing repair. Existing loader checks separately validate current Evidence selection and source-text containment, as well as current resources.

Capacity uses actual Python `len(str(context))`, including wrapper/escaping overhead. Frozen reviewed demonstration input is 44,370 characters before filtering and 10,282 afterward. Advisory remains output-dependent: `10,282 + len(str(complete_plan_dict)) + 36`; 30,000 accepts and 30,001 rejects. A valid oversized Plan output fails its originating Run before advisory calls or scientific persistence. No capacity-specific repair, truncation, extra provider call or new deadline is added.

Plan generation and advisory each retain at most two logical calls, including one structural repair. Both repairs receive unchanged complete payloads. With the existing retry_count=2 and 120 seconds per attempt, the bound remains four logical/adapter calls and twelve request-path attempts; there is no overall Plan deadline. Legacy cumulative attempt counters are not durable transport logs. Plan still does not acquire Hypothesis-specific tracing or GapCapture observability.

## Atomic persistence and bounded failure transitions

Before scientific persistence, the final Plan transaction locks its Run by exact ID, project and `experiment_plan` stage and requires `started`. Missing, terminal, wrong-project or wrong-stage Runs reject with `generation_superseded`. Plan insertion and the conditional `started -> succeeded` update commit together. Rowcount must be exactly one; otherwise the transaction rolls back every Plan write. No external terminal status is replaced.

Plan supplies a scoped failure callback to the existing generation/repair and advisory-capacity helpers, as well as final persistence error handling. It updates only that exact ID/project/experiment_plan/started record. This prevents a rejection or provider/validation error from marking an ownership-mismatched Run failed. Shared helper defaults remain the previous failure behavior for every other stage.

The existing Hypothesis success fence remains equivalent. Gap persistence and unrelated lifecycle behavior are not repaired by this patch. No cancellation endpoint, status recovery, concurrency framework, new deadline or durable-logging guarantee is introduced. Cancellation before final persistence can still leave a started Run; ambiguous commit/connection loss requires read-only reconciliation of the original operation without automatic resubmission or status overwrite. Run locking cannot guarantee API-response delivery.

Normal success remains exactly one model-origin Plan with its original Run and `pending_review`; no human acceptance or downstream action occurs automatically. Plan provenance remains through Hypothesis/Gap and their canonical source links; the current schema has no new independent Plan Evidence relationship table.

## Prompt and demonstration boundary

Only PLAN_PROMPT moves from m4-v1 to m4-v2. Generation, advisory, structured-repair and advisory-repair wording explain plan-context-v1, source roles and the visible citation boundary. The complete Plan prompt bundle digest is recomputed; Gap/Hypothesis prompt and historical schema identities remain unchanged.

For the selected human demonstration, delta_demo=1 score °C, assumed domain [-15.2,45.2], N_demo=150000, complete-count gate 127891, alpha=0.05 and support/contradiction/inconclusive rules remain hypothetical human choices. They are not empirical findings, meaningfulness, verified instruments/independence/feasibility or approved laboratory workload. Textual counts do not authorize datasets or physical runs. No canonical source expansion is implied.

Deployment, production generation and scientific Plan review remain separate human decisions. An eligibility flag establishes neither advisory output capacity nor generation permission.
