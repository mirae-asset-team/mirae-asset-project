# Public research UI release implementation plan

> **Required skill:** Execute this plan with `superpowers:executing-plans`. Apply `superpowers:test-driven-development` to every behavior change, `superpowers:systematic-debugging` to every unexpected failure, and `superpowers:verification-before-completion` before every commit and completion claim.

**Goal:** Turn the anonymous disclosure chat into a polished, publicly usable research UI that shows searchable corpus breadth, explains provider mode honestly, exposes verifier-owned filing excerpts, and gives a precise safe explanation for unsupported corpus-wide financial counts.

**Architecture:** Keep the existing FastAPI/static-JavaScript application and fail-closed agent pipeline. Add only additive public API fields (`company_count`, citation `excerpt`), derive every excerpt from admitted corpus evidence in the verifier, and keep client presentation in pure helpers plus DOM rendering. Self-host three unmodified KoPubWorld WOFF2 files and their license; do not add a runtime CDN or JavaScript framework.

**Stack:** Python 3.11+, FastAPI, dataclasses, vanilla ES modules, Node's built-in test runner, pytest, Docker Compose on the existing NCP server.

**Design spec:** `docs/superpowers/specs/2026-08-20-public-research-ui-design.md`

## Global constraints

- Treat the D-drive base database, live overlay, and live search index as read-only. Do not copy them into Git or a container image.
- Do not lower readiness, attestation, evidence, correction-lineage, completeness, or provider gates.
- The 76-company value means distinct searchable company candidates, not complete comparable financial coverage.
- Keep corpus-wide financial threshold/count questions unanswerable while `corpus_wide_complete=false`.
- Never print or commit the contest CLOVA key, NCP administrator password, PEM content, or raw provider response.
- Deploy code only to the existing NCP instance; create no new paid server, disk, database, IP, or managed service.
- End every task with fresh task-specific verification and one focused commit.

## Task 1: Publish the executable plan

**Files:**

- Add: `docs/superpowers/plans/2026-08-20-public-research-ui-release.md`

1. Confirm the document has no unresolved marker, placeholder path, or undecided implementation choice.
2. Confirm every requested UI/API/deployment outcome appears in a task and every task has a verification command and commit.
3. Run:

   ```powershell
   git diff --check
   ```

   The command must exit zero.
4. Commit:

   ```powershell
   git add docs/superpowers/plans/2026-08-20-public-research-ui-release.md
   git commit -m "docs: plan public research ui release"
   ```

## Task 2: Report searchable company count from the cached ready corpus

**Files:**

- Modify: `tests/test_agent_runtime.py`
- Modify: `tests/test_public_web.py`
- Modify: `src/disclosure_db/api.py`

1. Add a route test whose ready service returns three literal candidates and assert `GET /health` returns `company_count == 3` without an extra candidate scan per health request. Add a degraded-service test asserting `company_count == 0`.
2. Run RED:

   ```powershell
   $env:PYTHONPATH='src'; python -m pytest tests/test_agent_runtime.py tests/test_public_web.py -q
   ```

   Confirm failure is the missing `company_count` contract.
3. In `create_app`, cache candidate count during the existing startup warm-up only when readiness is true. Include `company_count` in each health payload, defaulting to zero before/when not ready. Do not query the database on each health request.
4. Run GREEN with the command from step 2, then run `git diff --check`.
5. Commit:

   ```powershell
   git add src/disclosure_db/api.py tests/test_agent_runtime.py tests/test_public_web.py
   git commit -m "feat: report searchable company count"
   ```

## Task 3: Attach bounded corpus-owned excerpts to verified citations

**Files:**

- Modify: `tests/test_agent_runtime.py`
- Modify: `src/disclosure_db/agent_contracts.py`
- Modify: `src/disclosure_db/answer_verifier.py`
- Modify: `src/disclosure_db/api.py`

1. Add verifier tests proving:
   - a verified citation receives whitespace-normalized text from the matching `EvidenceRef`;
   - the excerpt is at most 600 characters;
   - unknown/provider-nominated citation IDs cannot create excerpts;
   - an abstention has no evidence excerpt in the public query response.
2. Add an API test asserting the additive `excerpt` field is serialized inside `evidence` for a verified answer.
3. Run RED:

   ```powershell
   $env:PYTHONPATH='src'; python -m pytest tests/test_agent_runtime.py -q
   ```

   Confirm failures are caused by the absent field/normalizer.
4. Add `excerpt: str | None = None` to `CitationRef`. Add a private verifier helper equivalent to:

   ```python
   def _citation_excerpt(text: str) -> str | None:
       normalized = " ".join(text.split())
       return normalized[:600] or None
   ```

   Populate it only while hydrating citations from `evidence_by_id`. Preserve the canonical failure behavior and never accept excerpt text from the generator/provider.
5. Keep `/query` serialization additive; the existing `to_jsonable()` path should carry the new field. Do not create a public evidence-by-ID endpoint.
6. Run GREEN with the command from step 3, then `git diff --check`.
7. Commit:

   ```powershell
   git add src/disclosure_db/agent_contracts.py src/disclosure_db/answer_verifier.py src/disclosure_db/api.py tests/test_agent_runtime.py
   git commit -m "feat: expose verified evidence excerpts"
   ```

## Task 4: Explain unsupported corpus-wide financial counts precisely

**Files:**

- Modify: `tests/test_evidence_service.py`
- Modify: `tests/web_api.test.mjs`
- Modify: `src/disclosure_db/query_planner.py`
- Modify: `src/disclosure_db/web/api.js`

1. Add planner tests for `영업이익 10억 넘는 기업은 몇 개야?` and `매출액 1조 이상 회사 수` asserting:
   - `fact_domain == "financial"`;
   - `company is None`;
   - reason code includes `corpus_wide_financial_coverage_required`;
   - the existing `company_unresolved` gate remains present.
2. Add Node tests for a pure `answerText(body)` helper. It must return the server answer normally, but return this literal when that reason code is present:

   ```text
   현재 검증된 재무 데이터가 전체 기업을 포괄하지 않아 기업 수 집계를 제공할 수 없습니다.
   ```
3. Run RED:

   ```powershell
   $env:PYTHONPATH='src'; python -m pytest tests/test_evidence_service.py -q
   node --test tests/web_api.test.mjs
   ```

4. Detect cross-company count/threshold intent only when an account term is present, no company resolves, and count/universe language such as `기업`, `회사`, `몇 개`, or `회사 수` co-occurs with a comparison/count expression. Append the stable reason code; do not make the plan answerable.
5. Implement `answerText(body)` as a presentation mapping over server-owned reason codes. It must not replace other abstention reasons.
6. Run GREEN with the commands from step 3, then `git diff --check`.
7. Commit:

   ```powershell
   git add src/disclosure_db/query_planner.py src/disclosure_db/web/api.js tests/test_evidence_service.py tests/web_api.test.mjs
   git commit -m "feat: explain incomplete financial aggregates"
   ```

## Task 5: Vendor KoPubWorld assets and build the compact research shell

**Files:**

- Add: `src/disclosure_db/web/fonts/KoPubWorld-Dotum-Bold.woff2`
- Add: `src/disclosure_db/web/fonts/KoPubWorld-Batang-Medium.woff2`
- Add: `src/disclosure_db/web/fonts/KoPubWorld-Batang-Bold.woff2`
- Add: `src/disclosure_db/web/fonts/LICENSE.md`
- Modify: `pyproject.toml`
- Modify: `tests/test_public_web.py`
- Modify: `src/disclosure_db/web/index.html`
- Modify: `src/disclosure_db/web/app.css`

1. Add behavior-level web tests that request the root, CSS, and font URLs and assert:
   - the removed subtitle is absent;
   - service information and corpus-fact elements are accessible by stable IDs;
   - the three local WOFF2 responses are non-empty and served by the app;
   - the page itself has no external URL dependency.
2. Add a package test that builds a wheel and inspects its archive members for all three WOFF2 files and `fonts/LICENSE.md`.
3. Run RED:

   ```powershell
   $env:PYTHONPATH='src'; python -m pytest tests/test_public_web.py -q
   ```

4. Download the unmodified files from the pinned `font-kopubworld@1.0.3` release. Before copying, inspect its `css.local` and `LICENSE.md`; keep the license beside the assets. Record source/version and SHA-256 values in the development log, but do not add npm as a runtime dependency.
5. Add `web/fonts/*.woff2` and `web/fonts/LICENSE.md` to setuptools package data.
6. Rewrite the semantic shell:
   - 224 px desktop navigation rail and existing accessible mobile toggle;
   - no purpose subtitle;
   - title only in KoPubWorld Dotum;
   - all other visible text in KoPubWorld Batang;
   - ready chip, service-info disclosure, corpus-fact chips, safe example questions;
   - centered 880 px reading column and compact message cards;
   - visible focus states and reduced-motion support.
7. Run GREEN with the command from step 3. Build a wheel into a temporary directory and inspect it, then run `git diff --check`.
8. Commit:

   ```powershell
   git add pyproject.toml src/disclosure_db/web/index.html src/disclosure_db/web/app.css src/disclosure_db/web/fonts tests/test_public_web.py
   git commit -m "feat: redesign public disclosure research ui"
   ```

## Task 6: Render honest service status and inspectable evidence

**Files:**

- Modify: `tests/web_api.test.mjs`
- Modify: `src/disclosure_db/web/api.js`
- Modify: `src/disclosure_db/web/app.js`

1. Add Node tests for pure helpers:
   - `healthLabel({ready: true, company_count: 76})` → `공시 DB 준비됨 · 76개 기업`;
   - `providerLabel(false)` → `검증형 기본 엔진 사용 · HyperCLOVA X 설명 미사용`;
   - `providerLabel(true)` → `HyperCLOVA X 설명 연결됨`;
   - `locatorLabel({kind: "table_row", table: 7, row: 16})` → `표 7 · 행 16`;
   - `dartUrl("20250822000109")` returns the safe DART filing URL;
   - malformed receipt numbers return `null`.
2. Run RED:

   ```powershell
   node --test tests/web_api.test.mjs
   ```

3. Implement the minimal helpers. Validate receipt numbers with exactly 14 ASCII digits and construct only `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...`.
4. Update `app.js` to:
   - use `answerText(body)` when storing assistant text;
   - set the ready chip from health data without warning styling merely because the provider is absent;
   - put provider mode and corpus revision in the service-information disclosure;
   - render one collapsed `공시 근거 N건` group;
   - insert excerpts via `textContent` only;
   - show Korean locator labels and an optional safe DART link;
   - render the empty-state corpus facts with the returned company count.
5. Run GREEN with the command from step 2. Run both web suites:

   ```powershell
   node --test tests/web_api.test.mjs tests/web_history.test.mjs
   ```

6. Commit:

   ```powershell
   git add src/disclosure_db/web/api.js src/disclosure_db/web/app.js tests/web_api.test.mjs
   git commit -m "feat: render research status and evidence details"
   ```

## Task 7: Record decisions and pass the complete local release gate

**Files:**

- Modify: `docs/development-log.md`
- Modify: `docs/operations/contest-release-checklist.md`
- Modify: `docs/operations/contest-server.md` if the current deployment procedure changed

1. Record the user-visible problems, chosen design, rejected alternatives, exact red/green commands, 76-company interpretation, incomplete aggregate gate, excerpt trust boundary, font source/version/hashes, provider secret boundary, and rollback procedure.
2. Add release-checklist entries for company-count truthfulness, evidence excerpt safety, provider-mode wording, local fonts, unsupported aggregate wording, insider-purchase regression, anonymous access, and read-only mounts.
3. Run the complete local gate fresh:

   ```powershell
   $env:PYTHONPATH='src'; python -m pytest -q
   node --test tests/web_api.test.mjs tests/web_history.test.mjs
   python -m compileall -q src
   git diff --check
   ```

4. Build a wheel in a temporary directory and inspect archive contents; run a local FastAPI smoke for `/`, `/health`, one verified query, the aggregate question, and the insider-purchase regression.
5. Commit only after all commands exit zero:

   ```powershell
   git add docs/development-log.md docs/operations/contest-release-checklist.md docs/operations/contest-server.md
   git commit -m "docs: record public ui release evidence"
   ```

6. Push the existing feature branch and confirm PR #1 reflects the new commits:

   ```powershell
   git push origin agent/disclosure-db-foundation
   gh pr view 1 --json url,headRefName,commits,statusCheckRollup
   ```

## Task 8: Deploy code only to the existing NCP server

**Files:**

- No tracked source changes unless deployment evidence requires a documentation-only follow-up commit.

1. Obtain the existing server administrator password through NCP using the existing PEM. Keep both out of output and Git. If unavailable, mark only remote deployment blocked; local release remains intact.
2. Re-attest the remote base DB, overlay, and search index modes/hashes against the recorded deployment manifest before changing application code. Stop on any mismatch.
3. Create an archive from the exact pushed commit. Inspect it to prove it contains no `.env`, key, PEM, SQLite/DB files, overlay, index, or secrets.
4. Upload only that archive to the existing server. Preserve the untracked remote `.env`; inject the contest CLOVA key from process memory without echoing it.
5. Rebuild/restart the existing Compose service without creating new cloud resources.
6. Run the public gate against `http://101.79.31.221:8000`:
   - `/` loads without login and contains the new title/structure;
   - `/health` is ready, reports the expected company count, and reports provider configuration truthfully;
   - one answerable query is verified and includes an excerpt;
   - `영업이익 10억 넘는 기업은 몇 개야?` abstains with the specific coverage explanation;
   - `최근 삼성바이오로직스 내부자매수 사례 찾아봐` remains unanswerable;
   - a valid citation exposes a safe DART link in the UI;
   - a container restart recovers to ready;
   - base DB, overlay, and search-index mounts remain read-only and hashes remain unchanged.
7. If the provider is configured, run the provider smoke and then the existing hard gate in `docs/superpowers/plans/2026-08-20-provider-final-gate.md`. Do not claim provider completion if credentials or external access block it.
8. Append deployment commit, timestamps, non-secret request IDs, health summary, mount/hash results, rollback location, and any explicit external blocker to `docs/development-log.md`. Commit and push the documentation update after fresh `git diff --check`.

## Final acceptance

- Every task has a focused commit and recorded test evidence.
- Full Python and Node suites pass from the final commit.
- Wheel contains local fonts and license.
- Public UI is anonymous, compact, KoPub-styled, and evidence-inspectable.
- Company count is described as searchable breadth, not financial completeness.
- Unsupported aggregate and insider-event questions fail closed with helpful wording.
- Provider state is honest and no secret is in Git/logs.
- Existing NCP deployment survives restart while all data mounts remain read-only.
