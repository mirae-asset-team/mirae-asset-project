# Contest server release checklist

Last updated: 2026-08-19 12:16:11 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.
`BLOCKED_LOCAL_RUNTIME` means a local runtime limitation prevented evidence collection; it is not treated as a pass.

| Item | Status | Evidence |
|---|---|---|
| Source/image commit | PASS | `3e41882` (`perf: prewarm company candidates before serving`; parents `06841c1`, `c804bfc`) |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| Live overlay SHA-256 | PASS | `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad` |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; run `20260818T214805Z-1b014f0bfca7`; `300/300` pass; all hard gates pass |
| Full local regression | PASS | `216 passed, 1 skipped, 17 subtests passed`; compileall passed |
| Provider configured | BLOCKED_EXTERNAL | No rotated `CLOVASTUDIO_API_KEY`; local value is `false` |
| Provider smoke/300-case provider pass | BLOCKED_EXTERNAL | Requires the missing rotated credential; no network call was made |
| Compose config hash | PASS | `compose.yaml` SHA-256 `C0EC24E3740C0574F376A9295B48D43AE181FEF8113625E631A2E052DC7635A2` |
| Docker image build/start | PASS | Docker Desktop 4.87.0 / Engine 29.7.2; Compose image built and container started |
| Docker `/health` | PASS | HTTP 200; ready/attested/search-ready true; request `aa6a926a29b14ea38b3f19b4c54b436f` |
| Docker `/query` smoke | BLOCKED_LOCAL_RUNTIME | D-drive bind-mounted query exceeded 120 seconds under Windows Docker Desktop; no query gate was marked pass |
| NCP server/public IP/ACG | BLOCKED_EXTERNAL | No NCP account/resource/credential is available |
| Data volume and remote artifact transfer | BLOCKED_EXTERNAL | No approved NCP host or external storage path is available |
| Local `/health` request ID | PASS | TestClient request `97de5985c7894355b2618abc38b5b01d`; HTTP 200; ready/attested/search-ready true |
| Local answerable request ID | PASS | `/query` request `a76c16260e274c61ab9a66cbf7e91341`; HTTP 200; q005 verified with 2 values and 2 citations |
| Local abstention request ID | PASS | `/query` request `722d23591a0742b0ba95128830511c47`; HTTP 200; answerable=false, verified=false, citations=[] |
| External public request IDs | BLOCKED_EXTERNAL | Requires NCP/public endpoint and second network |
| Local rollback artifact | PASS | Previous live overlay preserved as timestamped backup; base/search untouched |
| External second-network test | BLOCKED_EXTERNAL | No public endpoint or second network is available |

## Rollback path

The previous live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260819-061500.previous.sqlite`. The immutable base and live search index were not modified by the overlay promotion.

## Go/no-go

Local deterministic hard gates: GO.

Public contest submission: NO-GO until the provider credential and NCP/Docker/public-network evidence above are supplied and re-run.
