# Contest server release checklist

Last updated: 2026-08-19 06:32:39 KST

This checklist records binary evidence without credentials or raw provider responses. `BLOCKED_EXTERNAL` means the local gate is defined but the required external resource is unavailable; it is not treated as a pass.

| Item | Status | Evidence |
|---|---|---|
| Source/image commit | PASS | `962216a35de8179e5c5319683470601a061c2c4c` |
| Immutable base size/SHA-256 | PASS | `38,773,280,768` bytes / `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| Live overlay SHA-256 | PASS | `92ffe3ce1740153cfec457f5354685535a31cc5fe0d7722d61f1195f2ac1d3ad` |
| Live search SHA-256/revision | PASS | `e223a19fcbefd4757a39b71e2b73eed7c81d01f2b54d74ca82e761dac10a8793` / `safe-search-v1` |
| Overlay integrity/base attestation | PASS | `integrity_check=ok`, `overlay_matches_base=true`, event facts `1,423` |
| 300-case manifest | PASS | SHA-256 `1b014f0bfca75c8db6f6306dcdab80fff4cafb80dfb3709bf1e9d3bb8d8dd2a0`; `300/300` pass; all hard gates pass |
| Full local regression | PASS | `213 passed, 1 skipped, 17 subtests passed`; compileall passed |
| Provider configured | BLOCKED_EXTERNAL | No rotated `CLOVASTUDIO_API_KEY`; local value is `false` |
| Provider smoke/300-case provider pass | BLOCKED_EXTERNAL | Requires the missing rotated credential; no network call was made |
| Compose config hash | NOT_RUN | No verified Docker/Compose runtime is available |
| Docker image build/start | BLOCKED_EXTERNAL | Docker CLI/runtime unavailable |
| NCP server/public IP/ACG | BLOCKED_EXTERNAL | No NCP account/resource/credential is available |
| Data volume and remote artifact transfer | BLOCKED_EXTERNAL | No approved NCP host or external storage path is available |
| `/health` external request ID | NOT_RUN | Public endpoint was not started without external prerequisites |
| Answerable/abstention external request IDs | NOT_RUN | Public endpoint was not started without external prerequisites |
| Restart/rollback test | NOT_RUN | Requires a deployed service; local overlay rollback backup is preserved |
| External second-network test | BLOCKED_EXTERNAL | No public endpoint or second network is available |

## Rollback path

The previous live overlay is preserved at `D:\mirae-asset-project\db\agent\agent_overlay.20260819-061500.previous.sqlite`. The immutable base and live search index were not modified by the overlay promotion.

## Go/no-go

Local deterministic hard gates: GO.

Public contest submission: NO-GO until the provider credential and NCP/Docker/public-network evidence above are supplied and re-run.
