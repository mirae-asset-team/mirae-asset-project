# Free-form Disclosure Judgment Agent Design

## Status and objective

This design extends the deployed disclosure agent from exact structured lookup into evidence-bound free-form research and multi-filing analytical judgment. The agent may assess disclosed historical conditions, trends, contradictions, and risk signals. It must not recommend buying, selling, holding, position sizing, target prices, or expected investment returns.

The implementation runs in three engineering loops. Every loop follows design refinement, failing tests, minimal implementation, focused and full regression, adversarial evaluation, gap analysis, documentation, and an independent commit. NCP receives one atomic release only after Loop 3 passes every applicable hard gate.

The immutable D-drive source database, current live overlay, and current live search index remain read-only. New Gold records, evaluation artifacts, optional dense-pilot data, and release candidates are built in repository outputs or staging. Credentials, raw provider responses, PEM material, administrator passwords, large databases, and runtime `.env` files never enter Git.

## Current baseline and gaps

The deployed system already provides:

- attested SQLite source, audited financial/event overlay, correction-aware evidence, and sparse search;
- exact financial lookup, three-period comparison, deterministic calculations, and fail-closed corpus-wide aggregation;
- citation, numeric, prompt-injection, cross-filing, lineage, and answerability verification;
- 300/300 deterministic stress evidence, 856/856 financial release evidence, 20-request SQLite p95 of 86.94ms, public anonymous UI, restart recovery, and atomic rollback;
- 76 searchable names that resolve to 70 legal issuers, with incomplete financial coverage explicitly represented.

The gaps are:

- the planner routes most unknown wording directly to text retrieval without decomposing the research need;
- free-form Gold coverage is too small and is not representative of contracts, capital actions, governance, risk factors, management discussion, and multi-filing questions;
- an evidence bundle is a flat list and cannot express required evidence slots, support/contradiction, temporal ordering, or correction relationships;
- no public contract distinguishes a cited fact, a calculation, an attributed disclosure statement, and a bounded analytical assessment;
- any question containing broad future-oriented terms is currently refused, but there is no precise boundary between prohibited investment advice and permitted historical/risk assessment;
- existing stress cases are strong deterministic regressions but do not constitute an independent hostile evaluator suite for free-form synthesis and judgment;
- team continuation documents do not yet provide one path for adding a question type, creating Gold, evaluating it, and releasing it.

## Chosen architecture

### Alternatives considered

1. **Layered evidence graph with deterministic gates — chosen.** Keep structured fact and sparse retrieval as the source of authority, add query decomposition, evidence-slot completeness, an evidence graph, and an auditable judgment record. Use HyperCLOVA X only to propose or phrase bounded claims; deterministic code remains authoritative over source selection, numbers, calculations, citations, confidence, and recommendation policy.
2. **Embedding-first, LLM-heavy RAG — rejected as the default.** It offers broad semantic matching quickly but does not solve correction lineage, units, accounting scope, temporal cutoffs, entity identity, claim support, or completeness. It also creates unnecessary cost and an 8.4-million-fragment operational burden before a measured residual need exists.
3. **Rule-only question expansion — rejected as the complete solution.** It is safe for known financial and event patterns but brittle for diverse disclosure language and multi-filing research. Deterministic rules remain part of the chosen design, but not the only retrieval mechanism.

### Layer boundaries

The runtime consists of six bounded stages:

1. **Policy classification** distinguishes permitted disclosure analysis from investment recommendation, personal suitability, target-price, and unsupported prediction requests.
2. **Analysis planning** resolves issuer, cutoff, filing types, correction policy, subquestions, operations, and required evidence slots before retrieval.
3. **Hybrid evidence retrieval** routes structured financial/event slots to the overlay and free-form slots to sparse search plus bounded query expansion. A dense sidecar is optional and evaluation-gated.
4. **Evidence graph assembly** deduplicates evidence and records issuer, filing, period, correction lineage, support, contradiction, operand, and temporal relationships.
5. **Judgment construction** creates typed claims and bounded assessment labels from the graph. HyperCLOVA X may propose wording or attributed qualitative claims, but cannot create facts, citations, confidence, or calculations.
6. **Claim and answer verification** checks every returned claim against admitted graph nodes and the recommendation policy, then emits a verified answer or a structured abstention.

No stage may silently broaden the issuer set, use a superseded filing as current, infer a missing unit, cross a filing boundary without a declared graph edge, or treat retrieval score as evidence truth.

## Public analytical boundary

### Permitted outputs

The agent may conclude `improved`, `deteriorated`, `mixed`, `risk_signal`, `stable`, or `insufficient_evidence` for disclosed historical or current-as-of conditions such as:

- profitability, growth, leverage, liquidity proxies, and capital structure trends;
- contract size, duration, change, termination, or customer concentration when the required contract evidence is present;
- financing pressure shown by disclosed borrowing, bond, capital increase, or repayment events;
- correction materiality and whether a conclusion changes after the effective correction;
- governance or insider-transaction signals explicitly supported by admitted event facts;
- consistency or tension between management discussion and disclosed numeric outcomes;
- peer comparison only when the requested denominator and metric coverage pass the existing completeness gate.

### Prohibited outputs

The agent must refuse or safely reframe:

- buy, sell, hold, accumulate, reduce, short, position-size, or portfolio-allocation recommendations;
- target price, expected return, future stock direction, or probability not explicitly disclosed by an admitted source;
- personal suitability, risk tolerance, tax, legal, or regulated advisory conclusions;
- unsupported causal claims, speculation presented as fact, or conclusions based on missing required evidence slots;
- whole-universe counts, lists, ranks, or peer superlatives when legal-issuer coverage is incomplete.

A prohibited investment question may be reframed only as a clearly labeled historical disclosure analysis. The reframed response must state that it is not a transaction recommendation.

## Contracts

### Analysis plan

`AnalysisPlan` is additive to the existing `QueryPlan` and contains:

- normalized question and policy decision;
- resolved issuer(s), legal-issuer IDs, stock codes, and optional comparison set;
- `as_of`, accounting period, filing-date range, report types, and correction policy;
- `analysis_mode`: `lookup`, `freeform`, `multi_filing`, `judgment`, or `prohibited`;
- ordered `subquestions`;
- ordered `required_evidence_slots`;
- completeness rule and maximum evidence/token budget;
- requested judgment dimension and allowed conclusion labels;
- planner reason codes.

An `EvidenceSlot` declares a slot ID, domain (`financial`, `event`, or `text`), issuer, period, filing/report constraints, search concepts, minimum and maximum evidence count, whether the slot is mandatory, and the consequence of absence. A mandatory absent slot makes the judgment unanswerable; it is never treated as negative evidence.

### Evidence graph

`EvidenceGraph` contains immutable graph nodes hydrated from corpus-owned metadata and typed edges. Node kinds are `filing`, `evidence`, `financial_fact`, `event_fact`, and `calculation`. Allowed edge kinds are:

- `belongs_to_filing` and `belongs_to_issuer`;
- `corrects`, `corrected_by`, and `effective_version_of`;
- `period_precedes` and `same_period`;
- `supports` and `contradicts`;
- `operand_of`;
- `comparison_peer` for an explicitly requested, coverage-safe comparison.

Every evidence node retains filing ID, report name, filed date, issuer identity, locator, bounded excerpt, lineage status, current/effective status, and retrieval diagnostics. Provider output may reference graph node IDs but cannot create or mutate nodes or edges.

### Judgment record

`JudgmentRecord` is the auditable public explanation object. It contains:

- dimension, conclusion label, cutoff, issuer scope, and completeness status;
- typed `ClaimRecord` objects;
- supporting and counter-evidence IDs;
- deterministic calculations and rules;
- limitations, missing slots, and stable reason codes;
- derived confidence `high`, `medium`, or `low`;
- `investment_recommendation=false`.

Claim kinds are:

- `observed_fact`: an admitted structured fact or directly attributed filing statement;
- `calculated_comparison`: deterministic operands, formula, result, period, unit, and evidence;
- `disclosure_attribution`: a bounded paraphrase explicitly attributed to a cited filing excerpt;
- `bounded_assessment`: an allowed conclusion produced from named rules and already verified claims.

Each claim has a claim ID, text, evidence IDs, optional calculation/rule ID, counter-evidence IDs, and limitations. Confidence is never selected by the provider. It is derived from slot completeness, source status, agreement/contradiction, claim kind, and coverage. `High` requires complete mandatory slots, current/effective lineage, no unresolved contradiction, and fully verified claims. `Medium` permits disclosed limitations but no missing mandatory slot. Any missing mandatory slot, unsafe lineage, or unresolved contradiction yields `insufficient_evidence` rather than `low` confidence.

The system does not expose private chain-of-thought. It exposes the evidence ledger, formulas, named rules, counter-evidence, and limitations needed for an independent reviewer to reproduce the conclusion.

## Retrieval and synthesis behavior

### Query decomposition

Decomposition is bounded by a catalog of supported disclosure research dimensions. The initial catalog covers:

- financial performance and capital structure;
- material contracts and contract changes;
- capital raising, borrowing, bonds, and repayment;
- corrections and conclusion changes;
- insider transactions and governance events;
- business/risk-factor and management-discussion retrieval;
- cross-period and explicitly requested peer comparison.

Deterministic patterns resolve known dimensions first. HyperCLOVA X may return a strict decomposition schema for otherwise free-form wording. The runtime validates dimension IDs, issuer IDs, dates, slot counts, and budgets; malformed or unsupported decomposition falls back to a bounded text search or abstention.

### Multi-filing completeness

Multi-filing questions define their evidence slots before search. For example, profitability-and-financial-health requires income-trend, balance-sheet, and financing-event slots. A contract-change judgment requires original/current contract facts and correction/version evidence. Results cannot be declared complete merely because the top retrieved passages sound relevant.

Evidence from different filings may be combined only when the graph records why: same issuer and requested time series, effective correction lineage, named comparison peer, or explicit operand relationship. Cross-issuer evidence is prohibited unless comparison was requested and entity resolution is unambiguous.

### Qualitative claims

Provider-generated qualitative text is accepted only as a `disclosure_attribution` with cited graph nodes or as wording for an existing deterministic `bounded_assessment`. Numeric tokens must equal verified fact/calculation values. Causal language is accepted only when the cited filing itself attributes the cause; otherwise the response uses non-causal language such as “동시에 확인됩니다” or abstains from causation.

## Embedding decision

Embeddings are unnecessary for structured financial/event lookup and do not replace entity, period, correction, scope, unit, or claim verification. They may be useful for semantically varied qualitative questions whose relevant paragraphs are missed by sparse and expanded queries.

Loop 1 creates a separate, audited free-form Gold set and records a sparse baseline. Dense work is eligible only for residual targets that:

- belong to the text route;
- remain missed at Recall@20 after metadata filtering and query expansion;
- have exact filing/evidence targets and no unresolved source safety issue.

The first dense pilot is bounded to the residual evaluation candidates and at most 20,000 selected fragments. It is built as a new staging sidecar with model identity, input hashes, vector dimension, evidence mapping, cost, and deterministic manifest. It is promoted only if it improves Recall@20 by at least 0.05, preserves all citation/safety gates, adds no unsupported answer, stays within the public query p95 target of 2 seconds, and fits the NCP resource budget. Otherwise it is deleted from the release candidate and embeddings remain deferred.

Corpus-wide embedding, PostgreSQL/pgvector, and OpenSearch are separate infrastructure decisions. SQLite remains the attested source and serving database unless measured concurrency or retrieval gates fail. OpenSearch is not installed merely to satisfy an architecture checklist.

## Three engineering loops

### Loop 1: free-form retrieval

Deliverables:

- supported-dimension catalog and additive analysis-plan contracts;
- an agent-audited free-form Gold set covering contracts, capital actions, governance, corrections, business/risk factors, management discussion, and paraphrases;
- deterministic query expansion and metadata-constrained multi-query retrieval;
- free-form retrieval evaluator with route, target, Recall@5/20, MRR, wrong-issuer, wrong-version, and latency metrics;
- bounded dense pilot decision artifact, whether adopted or deferred.

Loop 1 hard gates:

- wrong issuer, wrong filing version, unsafe lineage, or unknown evidence: 0;
- free-form Recall@20 at least 0.95 on answer-safe targets;
- no mutation of source/live DB, overlay, or index;
- dense adoption only under the measured gate above.

### Loop 2: multi-filing judgment

Deliverables:

- evidence-slot planner, graph assembler, deterministic assessment rules, judgment contracts, and claim verifier;
- additive API response fields and UI cards for conclusion, rationale ledger, calculations, counter-evidence, limitations, confidence, and source expansion;
- judgment Gold cases for improvement/deterioration/mixed/risk/insufficient-evidence outcomes;
- strict recommendation-policy handling and safe historical-analysis reframing;
- strict provider schema smoke and a deterministic renderer for already verified judgment records.

Loop 2 hard gates:

- unsupported claim, false numeric claim, unverified calculation, unknown/cross-filing citation, missed effective correction, or recommendation output: 0;
- every answerable claim has admitted evidence and every bounded assessment names its verified input claims/rule;
- missing mandatory slots and unresolved contradictions yield `insufficient_evidence`;
- UI renders all external text through safe text APIs and preserves existing no-login/history behavior.

### Loop 3: hostile evaluation, team handoff, and release

Keep the existing 300-case suite unchanged and add 240 v2 cases grouped by issuer/filing so train and holdout groups cannot leak. The additional allocation is:

- free-form retrieval: 60;
- multi-filing synthesis: 50;
- analytical judgment: 50;
- recommendation boundary: 20;
- adversarial/entity/version/unit attacks: 30;
- provider/index/database/concurrency/restart faults: 30.

The hostile taxonomy includes:

- direct and disguised buy/sell/hold, target-price, expected-return, portfolio, and suitability requests;
- question and document prompt injection, role spoofing, malicious citation IDs, Unicode/spacing perturbation, and instruction-looking disclosure text;
- wrong issuer, subsidiary/parent confusion, ticker collision, and similarly named company attacks;
- original-versus-corrected, withdrawn, future-as-of, filing-date-versus-period, and stale evidence attacks;
- consolidated/separate, currency/scale, duration/instant, negative-sign, and formula attacks;
- missing evidence slots, contradictory filings, partial coverage, and evidence flooding;
- provider timeout/HTTP error/malformed or ambiguous JSON, search-index absence/staleness, read-only DB failure, lock contention, rate limit, oversized input, concurrent requests, container restart, and rollback;
- XSS, error-detail leakage, credential/secret extraction, history tampering, and unsupported URL/source injection;
- evaluator leakage, duplicate cases, metamorphic paraphrases, answer-order changes, and reproducibility failures.

Loop 3 hard gates are zero unsafe recommendation, false number, unsupported judgment, prompt-injection success, unknown/cross-filing citation, effective-version violation, credential leakage, evaluator error, and unhandled server error. Answerability agreement must be at least 0.95, numeric exactness and judgment evidence coverage 1.0, citation precision 1.0, citation recall at least 0.95, metamorphic consistency at least 0.98, fault isolation 1.0, and reproducibility 1.0.

A separate provider-required holdout runs only after the strict schema smoke passes. Provider failure never permits an unsafe answer or corrupts a verified structured result. If the external provider gate cannot pass, the public deterministic release may remain operational, but final provider-quality status is explicitly `NO-GO` and is never disguised as success.

## Team continuation documentation

The repository will add:

- `docs/team/README.md`: entry point, current state, roles, and reading order;
- `docs/team/architecture.md`: runtime/data/evidence/judgment flow and contracts;
- `docs/team/adding-question-types.md`: planner, slots, Gold, tests, API/UI, and release steps;
- `docs/team/evaluation-and-gold.md`: trust tiers, case authoring, review queues, stress gates, and anti-leakage rules;
- `docs/team/deployment-runbook.md`: local verification, secret handling, NCP atomic deploy, public smoke, rollback, and evidence recording;
- `docs/team/status-and-backlog.md`: completed, blocked, requested, prioritized next work, owners, and acceptance criteria;
- `CONTRIBUTING.md`: environment, branch, TDD, commit, secret, database, and review rules;
- `.github/pull_request_template.md`: test evidence, data identity, safety effects, docs, deployment, and rollback checklist;
- a dated final handoff covering exact commit, artifact hashes, public endpoint, known limitations, and next requests.

No document labels agent-generated review as human verification. Decisions record rejected alternatives, cost/resource implications, thresholds, and evidence. Request/backlog entries require an owner or `unassigned`, a concrete acceptance criterion, and a status; vague wish lists are not treated as work completed.

## Deployment and rollback

The final release uses the exact verified commit and a code-only archive. If no dense sidecar passes the gate, the current remote base, overlay, and search files remain byte-identical and read-only. If a dense sidecar passes, it is uploaded under a new content-addressed staging name, verified against its manifest, mounted read-only, and activated by atomic configuration/release-directory swap. It never modifies the immutable source DB or existing search index.

Pre-deploy and post-deploy checks include source/live hashes, SQLite quick/FK checks, overlay counts, manifest identity, container mounts, `.env` mode without content disclosure, full Python/Node/stress suites, public health, representative structured/free-form/judgment/abstention/adversarial queries, browser evidence expansion, 20 concurrent requests, container restart, and rollback presence. Deployment stops and rolls back on any hard-gate failure.

The public `/health` response reports configuration and corpus/retrieval/judgment revision identifiers but never credentials. The API remains anonymous under the existing rate and concurrency limits. Recommendation policy and judgment limitations are enforced in the API, not only in UI copy.

## Acceptance summary

The project is ready for final NCP promotion only when:

- all three loops have independent RED/GREEN evidence, evaluation artifacts, development-log entries, and commits;
- existing 304-test/16-web-test and release suites do not regress;
- new free-form, judgment, hostile, and provider-applicable gates pass without threshold reduction;
- every public analytical judgment is reproducible from its evidence ledger and never becomes investment advice;
- embedding and database decisions are justified by measured artifacts rather than architecture preference;
- team documentation permits a fresh contributor to add and evaluate a question type without accessing secrets or mutating the immutable corpus;
- rollback artifacts and public post-restart verification are present;
- code, docs, reproducible manifests, and non-secret reports are pushed to the approved GitHub branch.
