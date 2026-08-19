# Contest server release checklist

Last updated: 2026-08-20 02:23:34 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.
`BLOCKED_LOCAL_RUNTIME` means a local runtime limitation prevented evidence collection; it is not treated as a pass.

| Item | Status | Evidence |
|---|---|---|
| Runtime image source commit | PASS | `7ce4fb3` (`docs: add anonymous web ui operations and smoke`); clean source tree at build time |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| Live overlay SHA-256 | PASS | `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad` |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; fresh provider-disabled run `20260819T163934Z-1b014f0bfca7`; `300/300` pass; all hard counters `0`; all four quality metrics `1.0` |
| Full local regression | PASS | `235 passed, 1 skipped, 17 subtests passed` in `28.19s`; compileall passed |
| Frontend logic regression | PASS | Node `24.14.0`; history/API suites `10/10` passed; this Node release rejects the removed `--experimental-default-type=module` flag, so the equivalent current `node --test` command was used |
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
| Compose config hash | PASS | `compose.yaml` SHA-256 `eebdcf5a7604ae904fe8ffbb049f508a2f3abe9194bdfad510392e636c4886d1`; anonymous limits render as `120/4/8` |
| NCP Docker image build/start | PASS | Docker Engine 29.1.3 / Compose 2.40.3; image rebuilt from `3ad8dd8`, container healthy |
| NCP Docker `/health` | PASS | HTTP 200; ready/base-attested/overlay-attested/search-ready all true; provider false |
| NCP Docker `/query` smoke | PASS | Exact numeric returned 2 verified values/2 citations; textual returned 1 citation; out-of-scope and injection returned no values/citations; 1.66–1.71s internally |
| NCP server/public IP/ACG | PASS | Operational 2-vCPU/8GB server with public IP; inbound current-admin `/32` TCP 22 and public TCP 8000 only; outbound TCP 443; stale SSH `/32` removed |
| Current public API reachability | PASS | Fresh Windows-network probe on 2026-08-20: TCP 22 and 8000 open; `/health` HTTP 200 with ready/base/overlay/search true and provider false; q005 HTTP 200, verified/answerable, both expected values present, two evidence records, about 2.09s |
| Anonymous public web root | BLOCKED_EXTERNAL | Fresh `GET /` did not return HTTP 200, proving the new packaged UI is not on the public deployment. NCP console redirected to login and no attached Chrome session was available, so no cached/chat-exposed credential was used and no remote state was changed. |
| Public UI code-only deploy and re-attestation | NOT_RUN | Remote `0444` modes/hashes, Compose read-only mounts, exact source commit deployment, bounded `429`, browser, container restart, and host reboot checks require an authenticated NCP session. These gates remain open. |
| Data volume and remote artifact transfer | PASS | 100GB ext4 mounted at `/srv/mirae`; base, overlay, search, and attestation hashes/sizes matched; all files mode `0444` |
| Local `/health` request ID | PASS | TestClient request `97de5985c7894355b2618abc38b5b01d`; HTTP 200; ready/attested/search-ready true |
| Local answerable request ID | PASS | `/query` request `a76c16260e274c61ab9a66cbf7e91341`; HTTP 200; q005 verified with 2 values and 2 citations |
| Local abstention request ID | PASS | `/query` request `722d23591a0742b0ba95128830511c47`; HTTP 200; answerable=false, verified=false, citations=[] |
| Windows local script smoke | PASS | web/CSP, health, answerable, abstention and injection checks passed; request IDs `4f54b7714450401091f4b73544dab843`, `7607b1f542b3464d86509becc61506fb`, `92c202e38c3847dc97408bcf50657fea`, `154b0b9584384f13b5069f7314d7e081`; server stopped after evidence collection |
| External public request IDs | PASS | From the Windows development network: exact numeric `992c7539608d4a65aface324b0d375f9`, textual `1d4a14498e314e5ca905d510677d48dc`, out-of-scope `7092506341e74bd2bec1377691386e46`, injection `8a080cd7045547b9ba525268c889bec0` |
| Local rollback artifact | PASS | Previous live overlay preserved as timestamped backup; base/search untouched |
| Container restart recovery | PASS | Container returned healthy and all five smoke cases passed after `docker restart` |
| Host reboot recovery | PASS | `/srv/mirae` auto-mounted, four immutable artifacts remained `0444`, container auto-started healthy, internal and public q005 passed |
| External second-network test | PASS | Public health 90.82ms; four public queries 1.74–1.99s; schema, evidence mapping, abstention, and injection behavior passed after reboot and ACG cleanup |

## Rollback path

The previous live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260819-061500.previous.sqlite`. The immutable base and live search index were not modified by the overlay promotion.

## Go/no-go

Local deterministic hard gates: GO. Existing public API and its prior restart-recovery evidence: GO. Anonymous public web UI deployment: NO-GO (`BLOCKED_EXTERNAL`). API-only team demo: GO; browser UI team demo: NO-GO until the code-only NCP redeploy and Steps 4-6 are freshly verified.

Final contest submission: NO-GO until a rotated HyperCLOVA X credential is supplied and the bounded provider schema smoke plus 300-case provider pass complete. This missing external gate was not relaxed.
