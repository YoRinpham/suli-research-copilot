# M3 synthetic evaluation notes — 2026-09-28

These observations are from synthetic, non-private excerpts using `qwen3.8-max` and `landscape_generation` prompt `m3-v2`. They are manually inspected quality notes, not a scientific accuracy benchmark. The raw probe records are in the ignored local `data/` directory.

| Case | Observed item categories | Source IDs | Manual reading |
|---|---|---|---|
| Comparable 80% versus 60% on Dataset X under protocol P | `finding`, `finding`, `conflict` | Valid | Conflict text names the comparable conditions, cites both sides, and does not declare a winner. |
| 80% on Dataset X versus 60% on Dataset Y | `finding`, `finding`, `finding` | Valid | Describes non-comparability and does not create a conflict item. |
| Accuracy versus recall on Dataset X | `finding`, `finding`, `metric` | Valid | Identifies the metric mismatch without asserting a contradiction. |

The first `m3-v1` probe mislabeled the different-dataset case as `conflict` even while its text said the findings were not contradictory. That observation motivated prompt `m3-v2`, which explicitly forbids a conflict-category item for non-comparable conditions. The first true-conflict Claim probe timed out, and one intermediate different-dataset Landscape probe failed at the provider. Those failures are retained in local probe records. A later isolated `m3-v2` run returned valid structured outputs for each of the three Landscape cases above.

The full application smoke test subsequently generated synthetic Claims and a Landscape through the API with successful GenerationRuns and whitelisted source IDs. It confirms the integration path, not repeatable semantic quality. Groundedness, overclaiming, and conflict precision remain researcher judgments; the `manual_*` fields in the probe output are intentionally blank until a human reviewer scores them.

## 2026-09-29 M3 semantic gate follow-up

Eight focused synthetic cases were run through the real `qwen3.8-max` adapter and PostgreSQL persistence boundary using the opt-in test harness. Their latest successful records are in ignored local `data/m3-live-semantic-results.json`; rerun instructions are in this directory's README. All eight latest runs returned HTTP 201. Claim generator proposals and persisted statuses were:

| Scenario | Generator's final proposal | Persisted server status | Run attempts |
|---|---|---|---:|
| Association versus causation | Qualified association, `supported` | `supported` | 2 |
| Difference without significance test | Numeric difference with explicit no-test caveat, `supported` | `supported` | 2 |
| Unsupported comparison | Narrow A-only result after structural repair, `supported` | `supported` | 4 |
| Conditional finding | Retained condition X, `supported` | `supported` | 3 |

The four Landscape cases ended as follows:

| Scenario | Generator item categories | Persisted item categories | Run attempts |
|---|---|---|---:|
| Comparable true conflict | finding, finding, conflict | finding, finding, conflict | 3 |
| Different dataset | finding, finding | finding, finding | 2 |
| Metric mismatch | finding, finding | finding, finding | 2 |
| Different baseline summary | finding, finding | finding, finding | 2 |

The different-baseline persisted summary reports the two baseline-relative results without declaring a definitive absence of conflict. In these live samples, the generator did not emit an invalid causal/significance/comparison claim or a false conflict. The adversarial FakeLLM database tests, rather than this cooperative sample, verify that the server downgrades and rejects such proposals at persistence. Early live attempts intermittently failed or timed out at the provider; the latest successful records used the configured finite retry count. These eight synthetic examples are not a general model-accuracy estimate, and human GROUNDED/OVERCLAIM/conflict labels remain unfilled.
