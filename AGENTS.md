\# AGENTS.md



\## 0. Purpose



This file defines repository-wide instructions for AI coding agents working on 溯理 SULI.



These instructions apply to all implementation, refactoring, testing, documentation, and architectural work unless a task explicitly overrides them.



Before making significant product or architectural changes, read:



\- `docs/product-spec.md`

\- `docs/architecture.md`, if it exists



The product specification defines what the product should do.



This file defines how the codebase should be changed.



\---



\# 1. Project Identity



溯理 SULI is a hackathon MVP designed to help researchers transform scientific literature into:



Evidence



→ Claims



→ Research Landscape



→ Candidate Research Gaps



→ Falsifiable Hypotheses



→ Experiment Plans



The central product principle is:



> Every important scientific claim should be traceable to identifiable source evidence.



The system is intended to augment researcher cognition, not replace researcher judgment.



\---



\# 2. Priority Order



When requirements conflict, optimize in this order:



1\. scientific traceability

2\. hackathon demo reliability

3\. correctness

4\. simplicity

5\. testability

6\. maintainability

7\. user experience

8\. performance

9\. architectural sophistication



Do not sacrifice traceability or reliability for architectural novelty.



\---



\# 3. Source of Truth



Use the following hierarchy.



\## Product behavior



Source of truth:



`docs/product-spec.md`



\## System architecture



Source of truth:



`docs/architecture.md`



\## API contracts



Source of truth:



`docs/api-design.md`



\## Current implementation task



Source of truth:



the explicit user or developer task



If a requested implementation appears to conflict with the product specification or architecture, identify the conflict before silently changing the design.



Do not rewrite product requirements merely to fit an easier implementation.



\---



\# 4. Core Architectural Principle



The system must preserve this provenance chain:



Scientific Statement



→ Claim



→ Evidence



→ PaperChunk



→ Paper



→ Page



Evidence provenance must never be silently dropped.



Avoid designs where generated text becomes detached from the evidence that produced it.



\---



\# 5. Domain Model Rules



Treat the following as distinct concepts:



\- Paper

\- PaperChunk

\- Evidence

\- Claim

\- ResearchGap

\- Hypothesis

\- ExperimentPlan



Do not collapse these concepts into generic JSON blobs without a strong reason.



In particular:



PaperChunk is not automatically Evidence.



Evidence is not automatically a Claim.



Claim is not automatically a ResearchGap.



ResearchGap is not automatically a Hypothesis.



Preserve the semantic distinction between pipeline stages.



\---



\# 6. Evidence Rules



Evidence is a first-class domain object.



Every Evidence object should preserve enough provenance to identify its origin.



At minimum, evidence should be traceable to:



\- paper

\- paper chunk

\- page

\- source text



Never fabricate:



\- evidence IDs

\- paper titles

\- page numbers

\- citations

\- experimental results

\- source passages



If provenance is unavailable, represent the output as unsupported or uncertain rather than inventing provenance.



\---



\# 7. LLM Rules



LLM output must be treated as untrusted external input.



Never assume model output is:



\- valid JSON

\- factually correct

\- evidence-grounded

\- schema-compliant

\- complete



Always validate important structured outputs.



Prefer:



\- Pydantic models

\- JSON Schema

\- explicit enums

\- constrained fields



over free-form text parsing.



\---



\# 8. Qwen Integration Rules



Qwen is the primary LLM for the hackathon project.



However, business logic must not depend directly on a specific Qwen model name.



Create an abstraction such as:



`LLMProvider`



or equivalent.



The provider layer should handle:



\- model selection

\- API requests

\- retries

\- structured output handling

\- provider-specific parameters

\- provider errors



Domain services should depend on the abstraction, not the concrete SDK implementation.



Do not scatter Qwen API calls throughout route handlers or business logic.



\---



\# 9. Embedding Rules



Embedding generation must also be abstracted.



Use an interface such as:



`EmbeddingProvider`



or equivalent.



The retrieval layer should not depend directly on a specific embedding model.



Embedding model configuration should come from environment or application configuration.



\---



\# 10. Prompt Rules



Prompts are part of application behavior and should be treated as versioned source code.



Keep prompts centralized.



Recommended locations:



`backend/app/prompts/`



or an equivalent dedicated module.



Do not embed long prompts directly inside:



\- API route handlers

\- database models

\- unrelated service functions



Prompt changes that materially affect structured output behavior should be reviewed like code changes.



\---



\# 11. Structured Output Rules



Use structured outputs for core scientific objects.



Examples:



\- Claim

\- ResearchLandscape

\- ResearchGap

\- Hypothesis

\- ExperimentPlan



Avoid using free-form model text as the primary persistence format for these entities.



Structured outputs should:



\- have explicit schemas

\- be validated

\- reject malformed critical fields

\- preserve linked evidence IDs



\---



\# 12. Scientific Reasoning Rules



The model may:



\- summarize evidence

\- compare evidence

\- identify possible conflicts

\- propose candidate research gaps

\- generate hypotheses

\- propose experiment plans



The model must not silently convert limited evidence into universal scientific truth.



Use uncertainty explicitly.



Preferred concepts include:



\- confidence

\- uncertainty

\- assumptions

\- evidence\_for

\- evidence\_against

\- insufficient\_evidence



Avoid unsupported statements such as:



> "This is definitely a novel research gap."



Prefer:



> "Within the uploaded evidence set, this variable appears underexplored."



\---



\# 13. Human-in-the-Loop Rules



Do not design the MVP as a fully autonomous research agent.



The researcher should remain responsible for selecting:



\- relevant evidence

\- research gaps

\- hypotheses

\- experiment plans



The system may recommend.



The user decides.



Avoid automatically chaining major scientific decisions without user inspection unless explicitly requested later.



\---



\# 14. Scope Control



The current MVP focuses on:



Research Question



→ PDF Papers



→ Paper Parsing



→ Retrieval



→ Evidence



→ Claims



→ Research Landscape



→ Research Gaps



→ Hypotheses



→ Experiment Plans



Do not introduce the following unless explicitly requested:



\- multi-agent architecture

\- agent swarms

\- GraphRAG

\- Neo4j

\- large knowledge graphs

\- model fine-tuning

\- autonomous lab control

\- autonomous paper writing

\- web-scale academic crawling

\- distributed systems

\- Kubernetes

\- event-driven microservices

\- complex authentication

\- speculative infrastructure abstractions



Do not add technology solely to make the architecture appear more sophisticated.



\---



\# 15. Architecture Philosophy



Prefer:



simple explicit pipelines



over:



framework-heavy orchestration



Prefer:



plain Python services



over:



complex agent frameworks



when both can satisfy the requirement.



Prefer:



one reliable reasoning pipeline



over:



multiple interacting agents



for the MVP.



New abstractions should solve an existing problem, not a hypothetical future problem.



\---



\# 16. Backend Stack



Preferred backend stack:



\- Python

\- FastAPI

\- SQLAlchemy

\- PostgreSQL

\- pgvector

\- Pydantic

\- Alembic

\- pytest



Use Python type hints.



Use async code only where it provides clear value.



Do not mix sync and async database patterns casually.



Choose one database access pattern and use it consistently.



\---



\# 17. Backend Layering



Prefer a structure similar to:



API / Routes



→ Application or Service Layer



→ Domain Logic



→ Repository / Persistence Layer



→ External Providers



API route handlers should remain thin.



Route handlers should generally:



1\. validate request

2\. call service

3\. map service result to response



Do not place:



\- long prompts

\- retrieval algorithms

\- SQL queries

\- domain reasoning

\- model orchestration



directly in API routes.



\---



\# 18. Suggested Service Boundaries



Prefer explicit services such as:



\- `PDFService`

\- `ChunkingService`

\- `EmbeddingService`

\- `RetrievalService`

\- `EvidenceService`

\- `ResearchLandscapeService`

\- `ResearchGapService`

\- `HypothesisService`

\- `ExperimentPlanService`



Provider abstractions may include:



\- `LLMProvider`

\- `EmbeddingProvider`

\- `FileStorageProvider`



Do not create services merely for naming symmetry.



Each service should have a meaningful responsibility.



\---



\# 19. Database Rules



PostgreSQL is the primary database.



pgvector should be used for vector retrieval unless architecture changes explicitly.



Database design should preserve domain relationships.



Prefer relational columns and explicit relationships for core product entities.



Use JSON fields for:



\- flexible metadata

\- model diagnostics

\- non-critical extensible fields



Do not store the entire product state as opaque JSON.



\---



\# 20. Migration Rules



Use Alembic for schema migrations.



Do not manually mutate production-like schemas without migrations.



Each schema change should include:



\- migration

\- model update

\- relevant tests



During early prototyping, destructive migrations are acceptable only when explicitly understood.



\---



\# 21. PDF Processing Rules



PDF parsing must preserve provenance.



For each extracted chunk, retain at minimum:



\- paper ID

\- page number

\- chunk index

\- text



Retain section title when reliably detected.



Do not claim section precision when section extraction is uncertain.



Parsing failures should be visible.



Do not silently continue with empty or badly corrupted paper content.



\---



\# 22. Chunking Rules



Chunking should optimize retrieval while preserving source traceability.



Avoid:



\- arbitrary character slicing without sentence awareness

\- chunks that lose page provenance

\- irreversible merging of multiple pages without provenance



Chunk configuration should be configurable.



Keep chunking logic independently testable.



\---



\# 23. Retrieval Rules



Keep retrieval separate from reasoning.



The retrieval layer should return structured retrieval results.



Suggested fields:



\- chunk\_id

\- paper\_id

\- page\_number

\- section

\- text

\- similarity\_score



Do not hide retrieval inside LLM prompts.



A retrieval failure and a reasoning failure must be independently diagnosable.



\---



\# 24. Retrieval Quality Before Agent Complexity



Before adding:



\- query rewriting agents

\- reranking agents

\- multi-agent retrieval

\- GraphRAG



first verify that the basic pipeline works:



Query



→ Embedding



→ Vector Search



→ Top-K PaperChunks



→ Evidence Selection



Only add complexity when a measurable retrieval problem exists.



\---



\# 25. Frontend Stack



Preferred frontend stack:



\- Next.js

\- TypeScript



Keep API access in a dedicated client or data-access layer.



Avoid embedding backend contracts directly throughout visual components.



Use typed API models where practical.



\---



\# 26. Frontend UX Rules



The primary interface should emphasize research structure rather than chat.



Prefer UI elements such as:



\- project workspace

\- paper list

\- evidence cards

\- claim cards

\- conflicting evidence views

\- research gap cards

\- hypothesis cards

\- experiment plan views



A chat interface may exist, but should not become the primary data model.



\---



\# 27. Evidence UX Rules



Whenever practical, allow users to move from:



AI-generated statement



→ supporting evidence



→ paper



→ source page



The UI should make provenance visible rather than hiding it in tooltips or logs.



Evidence inspection is part of the core product experience.



\---



\# 28. Error Handling



Failures should be explicit.



Examples:



\- PDF parse failed

\- embedding request failed

\- LLM request failed

\- structured output invalid

\- no relevant evidence found

\- insufficient evidence for research gap

\- database failure



Do not convert all failures into generic success responses.



Do not silently retry indefinitely.



\---



\# 29. Configuration Rules



All environment-specific settings should come from configuration.



Examples:



\- database URL

\- Qwen API key

\- Qwen model

\- embedding model

\- chunk size

\- chunk overlap

\- retrieval top\_k



Never commit secrets.



Maintain an up-to-date `.env.example`.



\---



\# 30. Logging Rules



Log enough information to debug the pipeline.



Useful logging dimensions include:



\- request ID

\- project ID

\- paper ID

\- pipeline stage

\- provider

\- latency

\- error type



Do not log:



\- API keys

\- secrets

\- entire private documents unnecessarily



Keep logs useful for demo debugging.



\---



\# 31. Testing Philosophy



Every major pipeline stage should be independently testable.



Priority test areas:



1\. domain model validation

2\. PDF parsing

3\. chunking

4\. provenance preservation

5\. embedding provider abstraction

6\. retrieval

7\. structured LLM parsing

8\. evidence-to-claim relationships

9\. API contracts



Use deterministic mocks for LLM tests whenever possible.



Do not make the entire test suite depend on live Qwen API access.



\---



\# 32. Test Execution Rule



Never claim tests pass unless they were actually executed.



When completing a task:



1\. implement the smallest coherent change

2\. add or update tests

3\. run relevant tests

4\. inspect failures

5\. fix failures

6\. report exactly what was run



If tests cannot run, state why.



\---



\# 33. Mocking Rules



External providers should be mockable.



Mocks should exist for:



\- LLMProvider

\- EmbeddingProvider

\- file storage where appropriate



This is essential for reliable CI and Codex-driven iteration.



\---



\# 34. Code Quality



Prefer:



\- clear names

\- small functions

\- explicit types

\- cohesive modules

\- predictable control flow



Avoid:



\- giant service classes

\- global mutable state

\- deeply nested conditionals

\- magic constants

\- hidden side effects

\- premature design patterns



Optimize for code that another developer can understand quickly during a hackathon.



\---



\# 35. Dependency Rules



Before adding a new dependency, ask:



1\. Is this required for the current MVP?

2\. Does the standard library or existing dependency already solve it?

3\. Does this dependency materially reduce complexity?

4\. Will it introduce operational risk?



Avoid unnecessary framework accumulation.



\---



\# 36. Performance Rules



Do not prematurely optimize.



Prioritize obvious bottlenecks:



\- PDF parsing

\- embedding generation

\- vector retrieval

\- LLM latency



Batch operations when simple and safe.



Use caching only when a repeated computation is clearly expensive.



\---



\# 37. Security Basics



Even for a hackathon:



\- never commit API keys

\- validate uploaded file types

\- constrain upload size

\- sanitize file paths

\- avoid arbitrary code execution

\- use environment variables for secrets

\- do not expose raw internal stack traces to users



More advanced security can remain out of scope unless required.



\---



\# 38. Documentation Rules



When implementation changes architecture, update documentation.



Important documentation includes:



\- `docs/product-spec.md`

\- `docs/architecture.md`

\- `docs/api-design.md`

\- `README.md`



Do not let documentation describe a materially different system from the code.



\---



\# 39. Architectural Change Rule



Do not make major architectural changes silently.



Examples:



\- replacing PostgreSQL

\- introducing multi-agent orchestration

\- replacing RAG architecture

\- introducing GraphRAG

\- changing core domain relationships

\- changing provider abstraction

\- changing provenance strategy



For such changes:



1\. identify the problem

2\. explain the proposed change

3\. explain tradeoffs

4\. update architecture documentation

5\. implement only after the change is justified



\---



\# 40. Implementation Workflow for Coding Agents



For non-trivial tasks, use the following workflow.



\## Step 1



Read relevant project documentation.



\## Step 2



Inspect existing implementation before editing.



\## Step 3



Identify the smallest coherent change.



\## Step 4



Implement the change.



\## Step 5



Add or update tests.



\## Step 6



Run relevant tests.



\## Step 7



Review the diff for:



\- scope creep

\- provenance loss

\- unnecessary dependencies

\- architectural inconsistency



\## Step 8



Summarize:



\- what changed

\- what tests ran

\- any remaining limitations



\---



\# 41. Do Not Overbuild



This is a hackathon MVP.



Do not attempt to solve hypothetical scale problems before demonstrating product value.



Avoid implementing infrastructure for:



\- millions of papers

\- global distributed users

\- enterprise tenancy

\- massive GPU clusters

\- complex job orchestration



unless explicitly required.



A reliable demonstration for 5–20 papers is currently more valuable than speculative internet scale.



\---



\# 42. Definition of Done for a Feature



A feature is not done merely because it produces visible output.



A feature is done when:



\- it matches product scope

\- its domain behavior is clear

\- provenance is preserved where required

\- failure states are handled

\- important outputs are validated

\- relevant tests pass

\- documentation remains consistent



\---



\# 43. Core Non-Negotiables



Never violate these without explicit architectural review.



1\. Do not fabricate evidence.

2\. Do not lose evidence provenance.

3\. Do not treat LLM output as trusted data.

4\. Do not hide retrieval inside opaque reasoning.

5\. Do not make chat messages the core domain model.

6\. Do not introduce multi-agent architecture prematurely.

7\. Do not introduce GraphRAG prematurely.

8\. Do not fine-tune models for the MVP.

9\. Do not store secrets in source control.

10\. Do not claim tests were executed when they were not.



\---



\# 44. Final Engineering Principle



When deciding between two implementations, prefer the one that makes the following pipeline easiest to understand, test, debug, and demonstrate:



Research Question



→ Papers



→ PaperChunks



→ Evidence



→ Claims



→ Research Landscape



→ Research Gaps



→ Hypotheses



→ Experiment Plans



The repository should make this reasoning chain obvious both in the code and in the product.
