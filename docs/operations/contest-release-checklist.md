# Contest server release checklist

Last updated: 2026-08-19 23:34:18 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.
`BLOCKED_LOCAL_RUNTIME` means a local runtime limitation prevented evidence collection; it is not treated as a pass.

| Item | Status | Evidence |
|---|---|---|
| Runtime image source commit | PASS | `3ad8dd8` (`fix: resolve deployed alias configuration`) |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| Live overlay SHA-256 | PASS | `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad` |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; run `20260818T214805Z-1b014f0bfca7`; `300/300` pass; all hard gates pass |
| Full local regression | PASS | `221 tests passed, 1 skipped`; compileall and focused deployment/alias tests passed |
| Provider configured on NCP | BLOCKED_EXTERNAL | `/health.provider_configured=false`; no rotated credential was supplied and the credential previously pasted in chat was not reused |
| Provider HTTP smoke | PASS | HyperCLOVA endpoint returned HTTP 200; response body redacted |
| Provider adapter schema smoke | FAIL_CLOSED | Parser now accepts exactly one prose-wrapped JSON object and rejects ambiguity; the prior live text response still did not satisfy the exact schema, and no current credential is available for re-smoke |
| Provider 300-case pass | NOT_RUN | Stopped after schema smoke; no provider quality gate was claimed |
| Compose config hash | PASS | `compose.yaml` SHA-256 `C0EC24E3740C0574F376A9295B48D43AE181FEF8113625E631A2E052DC7635A2` |
| NCP Docker image build/start | PASS | Docker Engine 29.1.3 / Compose 2.40.3; image rebuilt from `3ad8dd8`, container healthy |
| NCP Docker `/health` | PASS | HTTP 200; ready/base-attested/overlay-attested/search-ready all true; provider false |
| NCP Docker `/query` smoke | PASS | Exact numeric returned 2 verified values/2 citations; textual returned 1 citation; out-of-scope and injection returned no values/citations; 1.66–1.71s internally |
| NCP server/public IP/ACG | PASS | Operational 2-vCPU/8GB server with public IP; inbound current-admin `/32` TCP 22 and public TCP 8000 only; outbound TCP 443; stale SSH `/32` removed |
| Data volume and remote artifact transfer | PASS | 100GB ext4 mounted at `/srv/mirae`; base, overlay, search, and attestation hashes/sizes matched; all files mode `0444` |
| Local `/health` request ID | PASS | TestClient request `97de5985c7894355b2618abc38b5b01d`; HTTP 200; ready/attested/search-ready true |
| Local answerable request ID | PASS | `/query` request `a76c16260e274c61ab9a66cbf7e91341`; HTTP 200; q005 verified with 2 values and 2 citations |
| Local abstention request ID | PASS | `/query` request `722d23591a0742b0ba95128830511c47`; HTTP 200; answerable=false, verified=false, citations=[] |
| Windows local script smoke | PASS | `scripts/start-agent.ps1 -Mode Local` plus `scripts/smoke-agent.ps1`; answerable and abstention cases passed; server stopped cleanly |
| External public request IDs | PASS | From the Windows development network: exact numeric `992c7539608d4a65aface324b0d375f9`, textual `1d4a14498e314e5ca905d510677d48dc`, out-of-scope `7092506341e74bd2bec1377691386e46`, injection `8a080cd7045547b9ba525268c889bec0` |
| Local rollback artifact | PASS | Previous live overlay preserved as timestamped backup; base/search untouched |
| Container restart recovery | PASS | Container returned healthy and all five smoke cases passed after `docker restart` |
| Host reboot recovery | PASS | `/srv/mirae` auto-mounted, four immutable artifacts remained `0444`, container auto-started healthy, internal and public q005 passed |
| External second-network test | PASS | Public health 90.82ms; four public queries 1.74–1.99s; schema, evidence mapping, abstention, and injection behavior passed after reboot and ACG cleanup |

## Rollback path

The previous live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260819-061500.previous.sqlite`. The immutable base and live search index were not modified by the overlay promotion.

## Go/no-go

Local deterministic hard gates: GO. Public endpoint and restart-recovery gates: GO. Team demo: GO.

Final contest submission: NO-GO until a rotated HyperCLOVA X credential is supplied and the bounded provider schema smoke plus 300-case provider pass complete. This missing external gate was not relaxed.
