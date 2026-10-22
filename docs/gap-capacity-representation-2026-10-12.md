# Gap request representation

This isolated proposal changes only Gap provider contexts and candidate capacity admission. No deployment is authorized by this document.

Gap generation prompt `m4-v3`, Run contract `m4-gap-v2`, and encoding `gap-context-v1` use fixed columns whose complete interpretation is versioned in Gap prompts. Domain rows retain full canonical IDs; typed references use row positions; shared Paper metadata and repeated Claim attribute strings are stored once. The decoder reconstructs all canonical source fields exactly. Public generated references and persistence use canonical IDs, and supporting/challenging roles stay separate. The existing public schemas, canonical manifest/digest, Claim/Landscape lineage, and historical/API reads remain unchanged.

Capacity is still Python `len(str(actual context))` with the configured 30,000 guard. The same encoded object is sent to the provider and counted. Static prompt/schema overhead is separately measured and is outside this existing context-only guard; this guard does not measure tokens or total request bytes. Source text, metadata and uncertainty are never shortened. Unknown source fields and conflicting identities fail explicitly.

Every candidate's exact independent advisory wrapper is checked before the first assessment. Capacity invalidity uses the existing single generation structured repair; persistent invalidity fails without a partial batch. Every assessment retains its existing single structured repair and immutable context. Clearly infeasible source inputs fail before Run creation. Source-specific allowance is supplied in generation instructions. Full scientific source authority is rechecked before Run creation and final persistence, independently of unspecified relationship row order, without changing canonical manifest shape.

The accepted fixture has 18 Claims, 19 Evidence records and five real items. Its verbose input is 36,325 characters; encoded input is 28,896; assessment wrapper overhead is 36; encoded candidate allowance is 1,068. The public schema allows unbounded assumptions, so fitting all schema-valid outputs is impossible to guarantee. Admission guarantees exact payload fit, not scientific adequacy. Concerns/misaligned advice remains visible, and generated Gaps remain candidates requiring human selection.

GapBatch allows 1–10 candidates. At N, the existing maximum is 2+2N logical calls and (2+2N)*(retry_count+1) transport attempts: 22/66 at N=10 and retry_count=2. Provider request deadlines do not establish an overall M4 workflow deadline. The existing Gap originating-Run status fencing limitation is unchanged by this capacity proposal.

See the accompanying review package for first-failing regressions, exact provider payloads and prompt/schema overhead, offline tests, disposable database cleanup, protected production integrity and byte-for-byte patch application proof. Live provider/probe/deployment/downstream/benchmark operations are excluded.
