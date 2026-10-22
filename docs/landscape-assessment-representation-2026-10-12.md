# Isolated proposal: assessment-context-v1

Review-only prototype: m3-v10 / m3-landscape-v9. Serving behavior remains m3-v9 /
m3-landscape-v8 until separately approved deployment. No migration or dependency.

The provider returns unchanged public LandscapeItemVerification JSON. Scientific
validators, source roles, distinct Evidence matching, comparability and Run/source
fences are unchanged. Complete canonical assessments remain verbose dictionaries
in storage/API with support_excerpts, source_binding_origin and
source_binding_schema_version. Full passages and paper/chunk/page/source-hash
provenance persist. Historical API reads do not invoke the encoder or rebind data.
Claim prompt/hash and public/historical scientific_schemas.py are unchanged.

The internal context projection excludes only the three binding fields already
excluded by v8. encode_assessment uses exact fixed columns in this order:

0. verdict
1. asserts_direct_conflict (boolean)
2. dimensions
3. support_verdict
4. flagged_statements (ordered list, including duplicates and empty list)
5. qualifications (ordered list, including duplicates and empty list)
6. evidence_refs (ordered complete unchanged original Evidence IDs)
7. justification (exact string)
8. server_direct_conflict_role (boolean, appended only for stored judgments)

Dimensions is an array ordered dataset, population, task, metric,
experimental_condition, baseline. All eight public columns are required even
when empty. Row length eight versus nine disambiguates the sole server column.
Unknown keys, missing keys, mixed alias/canonical names, invalid types, unknown
dimensions and unknown row/dimension lengths explicitly reject. The codec copies
mutable lists and roundtrips all scientific values exactly. No semantic enums or
source IDs are compressed. Pydantic remains responsible for scientific schema
validity. Malformed pre-validation responses defer to the unchanged public schema
so extra_forbidden retains sanitized diagnostics; they never become accepted rows.

item_assessments is the ordered list of nine-column rows; row i belongs to
accepted_items[i]. The provider's centrally versioned instructions describe every
column, dimensions and association and still require public JSON objects back.

## Measurement and reservations

Assessment 2500 now means Python len(str(actual encoded assessment list)). Current
quota means len(str(encoded eight-column candidate)). Historical v8 verbose and
v9 encoded sizes are not directly comparable. It is never JSON length or tokens.

assessment_payload supplies actual immutable prior, cumulative checks and the
actual summary verification/repair/reverification context. The same encoder
measures candidates, mechanical floors and incremental server role overhead:
False adds seven characters; True is one smaller. Exact list separators add two;
empty prior is two. Actual prior + current quota + role/list overhead + future
reservation conserves 2500. Structured repair keeps quota/sources; scientific
regeneration resets prior. Longest enums, mechanical flag/qualification/nonblank
justification slots and complete required-reference matching remain conservative
policy reservations, not proven minima or scientific adequacy guarantees.

Allocation diagnostics retain numeric fields and add the fixed
assessment_encoding_version=assessment-context-v1. Aggregate assessment errors
carry the same version. Prompt bundle hashes the exact interpretation. Unknown
extra-field names and scientific text remain redacted from diagnostics.

Critique 1500 is unchanged. rejected_conflict is a distinct partial critique with
text/category/comparison_dimensions, not a canonical item assessment; its actual
verbose shape and counter remain aligned. Summary critique, full item sources and
generation sources retain existing shapes. Larger assessment quotas cannot raise
the independent critique limit. All complete contexts remain capped at 30000.
Approved 18-Claim/19-Evidence output stays 4627. Existing verbose-derived
generation item-count guidance remains, rather than silently generating more items.

## Capacity and limits

Five synthetic rows with 4/4/4/4/3 references have 845 fixed field/enum/framing
characters, 750 complete UUID footprint characters and 905 available prose
characters. The comparable verbose shape has 2130 fixed characters and negative
380 prose capacity. A separate meaningful-prose regression using all 19 approved
UUIDs changes from 3485 to 2200 without changing any scientific value.

Five rows referencing all 19 IDs each remain infeasible: their ID footprint alone
is 3790. Fail closed without deleting/merging/regrouping items, removing required
references or qualifications, shrinking scope or shortening scientific strings.
These examples are explicitly synthetic, not discarded production content.
No finite fixture certifies every future scientific judgment. The operator
package quantifies A/B/C, six complete repair contexts including 29770, offline
and disposable-database checks, exact patches and integrity inventories. Cap
increases are a separate exception proposal, not implemented here.
