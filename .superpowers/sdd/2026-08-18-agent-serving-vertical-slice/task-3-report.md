# Task 3 report: remove full-corpus hashing from request paths

## Files changed

- `src/disclosure_db/attestation.py`: added `CorpusAttestation`, distribution-manifest loading, SHA-256 shape validation, and cached size/mtime fast identity checks.
- `src/disclosure_db/financial_overlay.py`: added attestation-aware overlay validation and attestation propagation to overlay fetches; legacy full-hash validation remains when no attestation is supplied.
- `src/disclosure_db/evidence_service.py`: threaded the loaded attestation through overlay checks and fetches so request-time calls cannot fall back to full hashing.
- `src/disclosure_db/agent.py`: added backward-compatible `AgentSettings` fields and startup-only attestation loading.
- `src/disclosure_db/cli.py`: added `--attestation` to `agent-query` and `serve`, using keyword settings construction.
- `src/disclosure_db/api.py`: health now reports attestation/base readiness and checks size/mtime and overlay SHA without hashing.
- `tests/test_attestation.py`: added manifest, fast-identity, and SHA-shape tests.
- `tests/test_financial_overlay.py`: added the regression test that patches `sha256_file` and verifies an attested overlay path.

## TDD evidence

RED command:

```text
$env:PYTHONPATH='src'; python -m unittest tests.test_attestation -v
...
ModuleNotFoundError: No module named 'disclosure_db.attestation'
```

The attested-overlay regression also failed at import time with the same expected missing-module error:

```text
$env:PYTHONPATH='src'; python -m unittest tests.test_financial_overlay.FinancialOverlayTests.test_attested_overlay_match_never_hashes_request_database -v
...
ModuleNotFoundError: No module named 'disclosure_db.attestation'
```

GREEN focused command:

```text
$env:PYTHONPATH='src'; python -m unittest tests.test_attestation tests.test_financial_overlay tests.test_agent_runtime -v
Ran 14 tests in 0.495s
OK
```

Additional verification:

```text
python -m compileall -q src tests
```

Full suite:

```text
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
Ran 63 tests in 1.984s
OK (skipped=1)
```

`git diff --check` was clean before commit.

## Commit

Implementation commit: `4866d96134aeccef869ab7d0e1be403432b5145a`

Message: `perf: attest agent databases without request-time hashing`

## Risks and deviations

- The distribution manifest supplies the trusted database byte count and SHA; `load_distribution_attestation` captures the database's current `st_mtime_ns` at startup, as specified, so later changes fail fast.
- Full SHA-256 remains used by offline overlay imports and by the legacy no-attestation validation path. The attested request path verifies only size/mtime plus the overlay's stored source SHA.
- `EvidenceService` was also modified even though it was omitted from the brief's file list; this is required because it owns the request-time overlay calls and otherwise would invoke the legacy full-hash path.
