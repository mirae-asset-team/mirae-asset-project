# Contest server release checklist

Last updated: 2026-08-21 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.
`BLOCKED_LOCAL_RUNTIME` means a local runtime limitation prevented evidence collection; it is not treated as a pass.

| Item | Status | Evidence |
|---|---|---|
| Runtime image source commit | PASS | exact code-only `git archive` from `dcf44b8`; archive SHA-256 `5e75b7b9e4e86c7d70e93761fb90a6f8f292c23df8835546dba53c006b182cdb`; no `.env`, database, key, or runtime file |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| NCP live overlay SHA-256 | PASS_PRIOR_RELEASE | `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`; corpus-wide overlay is not yet deployed remotely |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| Validated financial-fact seed coverage | PASS_SCOPE_LIMITED | Strict seed-to-audited-Gold match `8/8`, all trust tier `agent_audited`; exact question/fact ID, filing, value, scale, and evidence set; `corpus_wide_complete=false` |
| Sparse retrieval Recall@20 | PASS_RECORDED | Existing FTS+RRF metric preserved at `13/17 = 0.7647058824`; complete-question recall `0.75`; MRR `0.3449449856`; no threshold was reduced |
| Hybrid evidence retrieval | PASS | Requested K `20`, effective safe-bundle K `8`; same 17 targets recovered `17/17`; complete-question recall `1.0`; routes financial `8`, event `7`, text `2`; service errors, dependency failures, and residual targets `0` |
| Dense embedding/vector pilot | DEFERRED_NO_EVIDENCE | Residual text targets `0`; no paid embedding call or dense manifest was created because the current Gold gate shows no eligible miss |
| PostgreSQL/pgvector/OpenSearch serving | DEFERRED_NO_EVIDENCE | SQLite remains attested SSOT/rollback; no serving sidecar or OpenSearch resource is justified by current residual evidence, provisioned, or claimed complete |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; fresh final provider-disabled run `20260819T174202Z-1b014f0bfca7`; `300/300` pass; evaluator error and all hard counters `0`; all four quality metrics `1.0`; p95 `5193.46ms`; failures `0` bytes; summary SHA-256 `3ab5b0a5823112157a4be4219883bcdba46f4f859d47f5fc2c65b8a0d73230ce` |
| Full local regression | PASS | corpus-wide final run `304 passed, 1 skipped, 19 subtests passed`; compileall passed |
| Frontend logic regression | PASS | history/API suites `16/16` passed with the bundled Node runtime |
| Wheel web assets | PASS | wheel SHA-256 `78056aee8b9d800dae964ae4624848ec05622a0adb922ff15afbe50f2e2e58f2`; `index.html`, `app.css`, `app.js`, `history.js`, `api.js` present (`5/5`) |
| Local Docker image/UI smoke | PASS | Docker Engine `29.7.2`; image `sha256:c0adf3adcc07dfb09e2e0fdb8f235b973c1036592c5cc262d3760b6ac1721cd9`; container healthy; web/CSP and three query probes passed |
| Local Docker request IDs | PASS | health `3029125d830a488ca62743bc4e0f71a3`; verified `84799fc1e0304306a9ee230d68ace408`; abstention `d5838369907941b4b0bb520eaea6e0b6`; injection `104d496d9b014b328720d37fe174b9ee` |
| Local Docker bind-mount latency | PASS_WITH_WARNING | internal query latency `192174.44–207511.27ms` on Windows Docker Desktop D-drive bind mounts; direct local runtime remained `4.78–4.89s`; functional gate passed but this setup is not a performance reference |
| Browser desktop/360px/XSS | PASS | verified answer and evidence card, abstention, search `2→0→3`, three delete controls, no horizontal overflow, mobile menu/focus return; literal `<img src=x onerror=alert(1)>` rendered as exact text with `img=0`, dialog `false`, console errors `0` |
| Immutable artifacts after Docker | PASS | base/overlay/search size, UTC mtime and SHA-256 matched the pre-Docker values; container mounts reported base/agent/attestation `rw=false`, runtime `rw=true` |
| Provider configured on NCP | BLOCKED_EXTERNAL | `/health.provider_configured=false`; no rotated credential was supplied and the credential previously pasted in chat was not reused |
| Provider HTTP smoke | PASS | HyperCLOVA endpoint returned HTTP 200; response body redacted |
| Provider adapter schema smoke | FAIL_CLOSED | Parser now accepts exactly one prose-wrapped JSON object and rejects ambiguity; the prior live text response still did not satisfy the exact schema, and no current credential is available for re-smoke |
| Provider 300-case pass | NOT_RUN | Stopped after schema smoke; no provider quality gate was claimed |
| Provider-required runner | PASS | `--provider-mode required` now fails before cases when unconfigured, isolates checkpoint identity by mode/model, records p95 and explicit gate reasons, and cannot treat disabled fallback as provider evidence |
| Official example `GET /answer` | PASS_LOCAL | Five-field response contract, bounded public context/trace, abstention and anonymous request limiting passed focused tests; public NCP deployment is not yet performed |
| Technical proposal source/PDF | PASS | Korean Markdown plus visually inspected 12-page A4 PDF; all required sections, tables and architecture diagram present; invariant build reproduced identical SHA-256 twice: `b238a7d75a5604ea73207dc04c095e4d92ebf5d42750c98a233ba415f17cd0cc` |
| Evaluation API server specification | PASS_PUBLIC | Standalone request/response/error/limit/health/deployment contract for `GET /answer` and `POST /query`; the public no-login deployment returned the exact five-field official response and the detailed verified response |
| Compose config hash | PASS | `compose.yaml` SHA-256 `eebdcf5a7604ae904fe8ffbb049f508a2f3abe9194bdfad510392e636c4886d1`; anonymous limits render as `120/4/8` |
| NCP Docker image build/start | PASS | Docker Engine 29.1.3 / Compose 2.40.3; image rebuilt from exact commit `dcf44b8`; image `sha256:53d5c5969df4f44338deb298320534bbffd952019568e948e40a8c5cfeb3e84f`; container healthy |
| NCP Docker `/health` | PASS | HTTP 200; ready/base-attested/overlay-attested/search-ready all true; provider false |
| NCP Docker `/query` smoke | PASS | Exact numeric returned 2 verified values/2 citations; textual returned 1 citation; out-of-scope and injection returned no values/citations; 1.66–1.71s internally |
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
| Public research UI v2 NCP deployment | PENDING | code-only deployment, restart recovery, anonymous browser/evidence expansion, provider mode, and read-only remote mount/hash checks must pass before promotion |
| Corpus-wide financial release evaluation | PASS_LOCAL | staging overlay: 856/856 exact cases; latest 456, three-year 397, incomplete count/list/rank refusals 3; false numeric and ungrounded verified answers 0; Samsung regression passed |
| SQLite 20-request concurrency | PASS_LOCAL | synchronized 20-request wave; errors 0; p95 86.94ms; fixed gate <=2,000ms, so PostgreSQL/OpenSearch remains deferred |
| Local corpus-wide overlay promotion | PASS_LOCAL | atomic rename promotion; new live SHA-256 `a4491f2072766fcc11db65bad8c592c78696aea87132f3e7420857924938cb55`; `quick_check=ok`; FK violations `0`; base identity matched; `1,191` facts |
| Local corpus-wide post-promotion smoke | PASS_LOCAL | root/static/health/coverage HTTP 200; Samsung FY2025 consolidated revenue `333,605,938 백만원`, filing `20260310002820`, verified with one evidence; request `9ceee5941efb4337bee91bafd07e475d` |
| Refreshed technical proposal PDF | PASS | 12 pages; pypdf required-value checks and Poppler visual inspection passed; SHA-256 `a77d409c4f0821d9f36431c35f0cb767ae64d2834881408c1d90c39974f71c75` |
| Corpus-wide NCP deployment | BLOCKED_EXTERNAL | authenticated console session is a subaccount; NCP rejects `서버 인증키 변경` and requires a main account. The stopped server was restored to `운영중` and the prior public UI reloaded successfully; no key or remote artifact changed |
| GitHub corpus-wide branch publication | PASS | explicit approval received; commits `4bd23bd` through `7467e4f` pushed to `ksm12030-sudo/mirae-asset-project`, branch `agent/disclosure-db-foundation`; remote advanced `df6f64d..7467e4f` |

## Rollback path

The previous local live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260821-015205.pre-corpus-wide.sqlite`. Its SHA-256 is `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad`. The immutable base and live search index were not modified by the promotion. The remote NCP service has not yet received the corpus-wide overlay.

## Go/no-go

Local deterministic and corpus-wide financial hard gates: GO. Local atomic promotion: GO. Existing prior-release public API and restart-recovery evidence: GO. GitHub publication: GO. Corpus-wide NCP redeployment: BLOCKED_EXTERNAL on a main-account session.

Final contest submission: NO-GO until a rotated HyperCLOVA X credential passes the bounded schema/provider 300 gate. The public UI/`GET /answer`/restart checks, technical proposal, and API specification are complete; no external gate was relaxed.
