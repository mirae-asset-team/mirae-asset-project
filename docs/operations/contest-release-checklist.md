# Contest server release checklist

Last updated: 2026-09-02 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.
`BLOCKED_LOCAL_RUNTIME` means a local runtime limitation prevented evidence collection; it is not treated as a pass.
`BLOCKED_ENVIRONMENT` means the required local service or engine was unavailable; static or unit evidence must not be reported as image/runtime validation.

| Item | Status | Evidence |
|---|---|---|
| Runtime image source commit | PASS | exact code-only `git archive` from `dcf44b8`; archive SHA-256 `5e75b7b9e4e86c7d70e93761fb90a6f8f292c23df8835546dba53c006b182cdb`; no `.env`, database, key, or runtime file |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| NCP live overlay SHA-256 | PASS | corpus-wide overlay `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55`; `quick_check=ok`, FK violations `0`, financial facts `1,191` |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| Validated financial-fact seed coverage | PASS_SCOPE_LIMITED | Strict seed-to-audited-Gold match `8/8`, all trust tier `agent_audited`; exact question/fact ID, filing, value, scale, and evidence set; `corpus_wide_complete=false` |
| Sparse retrieval Recall@20 | PASS_RECORDED | Existing FTS+RRF metric preserved at `13/17 = 0.7647058824`; complete-question recall `0.75`; MRR `0.3449449856`; no threshold was reduced |
| Hybrid evidence retrieval | PASS | Requested K `20`, effective safe-bundle K `8`; same 17 targets recovered `17/17`; complete-question recall `1.0`; routes financial `8`, event `7`, text `2`; service errors, dependency failures, and residual targets `0` |
| Main `[agent]` NumPy boundary | PASS_LOCAL_CONTRACT / BLOCKED_ENVIRONMENT | `agent` extra has no NumPy declaration and the main `Dockerfile` installs only `.[agent]`; Dense alone pins `numpy==2.5.2`. Focused artifact tests pass, but Docker engine was unavailable, so no Task 1 image package inventory was measured. |
| Dense runtime identity contract | PASS_LOCAL_CONTRACT / BLOCKED_ENVIRONMENT | Startup fails closed unless the vector manifest declares `IndexFlatIP`/`inner_product`/normalized embeddings, the loaded FAISS index confirms the declared type and metric, and `/model/model_identity.json` matches the pinned model/revision. Manifest and `/health` publish only those validated values; existing health fields remain. No new Dense image or deployed health response was validated. |
| Current Task 1 compose/image/container measurement | BLOCKED_ENVIRONMENT | On 2026-09-02 the Docker Desktop Linux engine was unavailable. Docker was not retried in fix round 1, so the current Task 1 compose render, main/Dense image inventories, container startup, Dense runtime manifest, and container `/health` remain unmeasured. Historical rows below do not validate the current branch. |
| Dense embedding/vector adoption | UNVERIFIED | Compose declares `2,571,506` vectors and prior staging observations reported full-corpus Dense use, but Task 1 did not remeasure artifact identity, Recall@20 gain, wrong issuer/version count, or p95. Dense is not release-adopted on configuration evidence alone. |
| Sparse fallback | PASS_LOCAL_QUERY / UNVERIFIED_COLD_START | Missing index, invalid index, empty filtered result, and unavailable sidecar all preserve Sparse results in four focused tests. Compose still gates agent cold start on Dense `service_healthy`; restart/cold-start fallback is not yet demonstrated. |
| PostgreSQL/pgvector/OpenSearch serving | DEFERRED_NO_EVIDENCE | SQLite remains attested SSOT/rollback; no serving sidecar or OpenSearch resource is justified by current residual evidence, provisioned, or claimed complete |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; fresh final provider-disabled run `20260819T174202Z-1b014f0bfca7`; `300/300` pass; evaluator error and all hard counters `0`; all four quality metrics `1.0`; p95 `5193.46ms`; failures `0` bytes; summary SHA-256 `3ab5b0a5823112157a4be4219883bcdba46f4f859d47f5fc2c65b8a0d73230ce` |
| Full local regression | PASS | Judge Stress V2 Task 1 fix round 1 run `548 passed, 2 skipped, 58 warnings, 74 subtests passed`; warnings are existing FastAPI `on_event` deprecations. |
| Frontend logic regression | PASS | Current history/API suites `12/12` passed with Node `v24.14.0`. |
| Wheel web assets | PASS | wheel SHA-256 `78056aee8b9d800dae964ae4624848ec05622a0adb922ff15afbe50f2e2e58f2`; `index.html`, `app.css`, `app.js`, `history.js`, `api.js` present (`5/5`) |
| Local Docker image/UI smoke | PASS_HISTORICAL | Historical evidence from 2026-08-20 at release commit `dcf44b8`: Docker Engine `29.7.2`; image `sha256:c0adf3adcc07dfb09e2e0fdb8f235b973c1036592c5cc262d3760b6ac1721cd9`; container healthy; web/CSP and three query probes passed. This is not current Task 1 image evidence. |
| Local Docker request IDs | PASS_HISTORICAL | Historical evidence from 2026-08-20 at release commit `dcf44b8`: health `3029125d830a488ca62743bc4e0f71a3`; verified `84799fc1e0304306a9ee230d68ace408`; abstention `d5838369907941b4b0bb520eaea6e0b6`; injection `104d496d9b014b328720d37fe174b9ee`. |
| Local Docker bind-mount latency | PASS_HISTORICAL_WITH_WARNING | Historical evidence from 2026-08-20 at release commit `dcf44b8`: internal query latency `192174.44–207511.27ms` on Windows Docker Desktop D-drive bind mounts; direct local runtime remained `4.78–4.89s`; this setup is not a performance reference. |
| Browser desktop/360px/XSS | PASS_HISTORICAL | Historical evidence from 2026-08-20 at release commit `dcf44b8`: verified answer/evidence, abstention, history controls, responsive layout, and literal XSS-string rendering passed. |
| Immutable artifacts after Docker | PASS_HISTORICAL | Historical evidence from 2026-08-20 at release commit `dcf44b8`: base/overlay/search size, UTC mtime, and SHA-256 matched pre-Docker values; container mounts reported base/agent/attestation `rw=false`, runtime `rw=true`. |
| Provider configured on NCP | PASS_CONFIGURED | `/health.provider_configured=true`; credential value and `.env` contents were not printed or committed; configuration alone is not provider-quality evidence |
| Provider HTTP smoke | PASS | HyperCLOVA endpoint returned HTTP 200; response body redacted |
| Provider adapter schema smoke | FAIL_CLOSED | Parser now accepts exactly one prose-wrapped JSON object and rejects ambiguity; the prior live text response still did not satisfy the exact schema, and no current credential is available for re-smoke |
| Provider 300-case pass | NOT_RUN | Stopped after schema smoke; no provider quality gate was claimed |
| Provider-required runner | PASS | `--provider-mode required` now fails before cases when unconfigured, isolates checkpoint identity by mode/model, records p95 and explicit gate reasons, and cannot treat disabled fallback as provider evidence |
| Official example `GET /answer` | PASS_LOCAL | Five-field response contract, bounded public context/trace, abstention and anonymous request limiting passed focused tests; public NCP deployment is not yet performed |
| Technical proposal source/PDF | PASS | Korean Markdown plus visually inspected 12-page A4 PDF; all required sections, tables and architecture diagram present; invariant build reproduced identical SHA-256 twice: `b238a7d75a5604ea73207dc04c095e4d92ebf5d42750c98a233ba415f17cd0cc` |
| Evaluation API server specification | PASS_PUBLIC | Standalone request/response/error/limit/health/deployment contract for `GET /answer` and `POST /query`; the public no-login deployment returned the exact five-field official response and the detailed verified response |
| Compose config hash | PASS_HISTORICAL | Historical evidence from 2026-08-20 at release commit `dcf44b8`: `compose.yaml` SHA-256 `eebdcf5a7604ae904fe8ffbb049f508a2f3abe9194bdfad510392e636c4886d1`; anonymous limits rendered as `120/4/8`. This does not validate the current Task 1 compose. |
| NCP Docker image build/start | PASS_HISTORICAL | Historical evidence from 2026-08-21 at corpus release commit `d3e909a`: image `sha256:9ab930b8bf84ad2c54fdbadb5e9ed2b0a41dd545ae8addf759edde53602117bea`; container healthy before and after explicit restart. This is not current Task 1 image evidence. |
| NCP Docker `/health` | PASS_HISTORICAL | Historical evidence from 2026-08-21 at corpus release commit `d3e909a`: HTTP 200; ready/base-attested/overlay-attested/search-ready true; `company_count=76`; post-restart request `70cb5e5e99404f8f882b86a909b3198c`. |
| NCP Docker `/query` smoke | PASS_HISTORICAL | Historical evidence from 2026-08-21 at corpus release commit `d3e909a`: exact numeric/textual/out-of-scope/injection probes passed at `1.66–1.71s` internally. |
| NCP server/public IP/ACG | PASS | Operational 2-vCPU/8GB server with public IP; inbound current-admin `/32` TCP 22 and public TCP 8000 only; outbound TCP 443; stale SSH `/32` removed |
| Current public API reachability | PASS | Fresh Windows-network probe on 2026-08-20: TCP 22 and 8000 open; `/health` HTTP 200 with ready/base/overlay/search true and provider false; q005 HTTP 200, verified/answerable, both expected values present, two evidence records, about 2.09s |
| Anonymous public web root | PASS | Public `GET /`, CSS and JS returned HTTP 200 with CSP; no login required. Browser showed the sidebar, search, new conversation, verified answer, two evidence cards, and persisted history. |
| Public UI code-only deploy and re-attestation | PASS | Exact `dcf44b8` code-only release deployed; `.env` preserved without disclosure; base/agent/attestation mounts remained `rw=false`; internal limiter reached `429` on request 121; public answerable/abstention/injection probes, container restart, host reboot, browser reload, and post-reboot full hashes all passed. Previous app is preserved for rollback. |
| Data volume and remote artifact transfer | PASS | 100GB ext4 mounted at `/srv/mirae`; base, overlay, search, and attestation hashes/sizes matched; all files mode `0444` |
| Local `/health` request ID | PASS | TestClient request `97de5985c7894355b2618abc38b5b01d`; HTTP 200; ready/attested/search-ready true |
| Local answerable request ID | PASS | `/query` request `a76c16260e274c61ab9a66cbf7e91341`; HTTP 200; q005 verified with 2 values and 2 citations |
| Local abstention request ID | PASS | `/query` request `722d23591a0742b0ba95128830511c47`; HTTP 200; answerable=false, verified=false, citations=[] |
| Windows local script smoke | PASS | web/CSP, health, answerable, abstention and injection checks passed; request IDs `4f54b7714450401091f4b73544dab843`, `7607b1f542b3464d86509becc61506fb`, `92c202e38c3847dc97408bcf50657fea`, `154b0b9584384f13b5069f7314d7e081`; server stopped after evidence collection |
| External public request IDs | PASS | From the Windows development network: exact numeric `992c7539608d4a65aface324b0d375f9`, textual `1d4a14498e314e5ca905d510677d48dc`, out-of-scope `7092506341e74bd2bec1377691386e46`, injection `8a080cd7045547b9ba525268c889bec0` |
| Local rollback artifact | PASS | Previous live overlay preserved as timestamped backup; base/search untouched |
| Container restart recovery | PASS | New image returned healthy after `docker restart`; read-only mounts and public root/health/q005 passed; request `36b4782a214c49f98220b131b9eaacdf` returned both values and two evidence records |
| Host reboot recovery | PASS | A real outage and recovery were observed; `/srv/mirae` auto-mounted, four immutable artifacts remained `0444` with all SHA-256 values unchanged, the new image auto-started healthy, public health recovered, and the browser UI/history reloaded successfully |
| External second-network test | PASS | Public health 90.82ms; four public queries 1.74–1.99s; schema, evidence mapping, abstention, and injection behavior passed after reboot and ACG cleanup |
| Public research UI v2 local gate | PASS_LOCAL | 224px desktop rail, 880px reading column, 390px responsive view without horizontal overflow; removed subtitle; title uses KoPubWorld Dotum and all other visible text uses KoPubWorld Batang |
| Searchable company count contract | PASS_LOCAL | real read-only runtime returned company_count=76; UI labels this as searchable companies and does not imply corpus-wide financial completeness |
| Verified evidence excerpt contract | PASS_LOCAL | verifier-owned normalized excerpts are bounded to 600 characters, omitted on abstention, rendered with textContent, and paired with a DART link only for 14-digit receipts |
| Incomplete aggregate explanation | PASS_LOCAL | the operating-profit threshold-count query remained unanswerable with corpus_wide_financial_coverage_required; no partial count was emitted |
| Insider-purchase regression after UI release | PASS_LOCAL | the Samsung Biologics insider-purchase query remained unanswerable with validated_event_fact_required |
| Local font packaging | PASS | pinned unmodified font-kopubworld@1.0.3; three WOFF2 files plus license present in the wheel; no runtime CDN |
| Public research UI v2 NCP deployment | PASS | anonymous public UI served from corpus release; browser returned the verified Samsung answer and evidence state; restart recovery and read-only mount/hash checks passed |
| Corpus-wide financial release evaluation | PASS_LOCAL | staging overlay: 856/856 exact cases; latest 456, three-year 397, incomplete count/list/rank refusals 3; false numeric and ungrounded verified answers 0; Samsung regression passed |
| SQLite 20-request concurrency | PASS_LOCAL | synchronized 20-request wave; errors 0; p95 86.94ms; fixed gate <=2,000ms, so PostgreSQL/OpenSearch remains deferred |
| Local corpus-wide overlay promotion | PASS_LOCAL | atomic rename promotion; new live SHA-256 `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55`; `quick_check=ok`; FK violations `0`; base identity matched; `1,191` facts |
| Local corpus-wide post-promotion smoke | PASS_LOCAL | root/static/health/coverage HTTP 200; Samsung FY2025 consolidated revenue `333,605,938 백만원`, filing `20260310002820`, verified with one evidence; request `9ceee5941efb4337bee91bafd07e475d` |
| Refreshed technical proposal PDF | PASS | 12 pages; pypdf required-value checks and Poppler visual inspection passed; SHA-256 `a77d409c4f0821d9f36431c35f0cb767ae64d2834881408c1d90c39974f71c75` |
| Corpus-wide NCP deployment | PASS | original PEM was found and used after explicit approval, so no key change was needed; code `d3e909a`, overlay `a4491f20...b55`, `1,191` facts, atomic rollback artifacts, public Samsung regression, restart recovery, and four post-deploy hashes passed |
| GitHub corpus-wide branch publication | PASS | explicit approval received; commits `4bd23bd` through `7467e4f` pushed to `ksm12030-sudo/mirae-asset-project`, branch `agent/disclosure-db-foundation`; remote advanced `df6f64d..7467e4f` |

## Rollback path

The previous local live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260821-015205.pre-corpus-wide.sqlite`. On NCP, the previous app is `/srv/mirae/releases/app-before-d3e909a-20260821`, the previous overlay is `/srv/mirae/data/agent/agent_overlay.20260821-pre-corpus.sqlite`, and the rollback image tag is `app-disclosure-agent:rollback-d3e909a`. The previous overlay SHA-256 is `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`. The immutable base and live search index were not modified.

## Go/no-go

Local deterministic and corpus-wide financial hard gates: GO. Local and NCP atomic promotion: GO. Public API/UI, post-restart recovery, immutable-hash re-attestation, and GitHub publication: GO.

Final contest submission: NO-GO until a rotated HyperCLOVA X credential passes the bounded schema/provider 300 gate. The public UI/`GET /answer`/restart checks, technical proposal, and API specification are complete; no external gate was relaxed.

Judge Stress V2 Task 1: local dependency, manifest, health, and query-time fallback contracts are ready; Docker image/runtime validation is `BLOCKED_ENVIRONMENT` and remains a release gate.
