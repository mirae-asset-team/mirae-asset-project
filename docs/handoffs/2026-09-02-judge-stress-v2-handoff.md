# Judge Stress V2 인수인계 — 2026-09-03

## 결론

`agent/judge-stress-v2`의 Task 1~8 구현을 바탕으로 `perf/search-index-validation-v2`에서 검색 인덱스 수명, 구조화 슬롯 완전성, relevance-set Gold V2를 보완했다. 실제 600건 앱/provider 평가와 운영 승격은 완료된 것으로 주장하지 않는다. 2026-09-05 최신 통합 release gate는 `BLOCKED_HARD_GATE`이며 31개 차단 사유가 남아 있어 8001/8000 및 NCP 배포를 실행하지 않았다. 다음 작업은 private holdout 120건과 실제 staging provider/identity를 준비해 같은 gate를 재실행하는 것이다.

> 2026-09-05 검색·재무 최종 갱신: 검색 인덱스는 프로세스 시작 시 한 번만 무결성 검증하도록 변경했고, 수익성·재무건전성은 선언된 `3계정 × 2기간` 6개 슬롯을 모두 요구한다. broad 질문의 유효한 대체 공시를 relevance set으로 평가하는 Gold V2에서 120 cases, 378/378 required hits, Recall@20 `100%`, slot completeness `100%`, wrong issuer/version `0`, p95 `103.07ms`를 실측했다. 최신 재무 회귀는 856/856, 삼성전자 최신 매출 회귀 통과, 허위 수치·근거 없는 검증 답변 `0`, 20동시 요청 오류 `0`, p95 `125.71ms`다. 이 결과는 private 600건/provider/staging identity를 대체하지 않는다.

> 2026-09-05 최종 재검토 갱신: 원격 `main` `f9b6ad1` 위에서 Dense sidecar 장애가 agent 기동을 막지 않도록 release Compose를 수정(`e88854a`)했고, 다중 기업 파생비율이 모든 기업의 operand를 보존하도록 수정했다(`0fb3a07`). 실제 read-only DB에서 에스엠/삼성전자 최근 영업이익률과 citation 4건을 확인했고 전체 Python `943 passed, 2 skipped, 264 subtests`, Web `13/13`을 통과했다. 그러나 새 120-case 검색 평가도 Recall@20 `0.487179...`, p95 `31.29s`였고 fresh gate는 사유 33개의 `BLOCKED_HARD_GATE`다. private holdout/provider/trusted deployment identity와 검색 95%가 충족되기 전에는 8000을 덮어쓰지 않는다. 기존 8000은 HTTP 200·`ready=true` 상태로 유지했다.

> 2026-09-03 Task 8 갱신: 원시 결과 재계산, 변조 방지, all-citation 검증, 동시성/보안 카운터, freshness와 외부 trust anchor를 하나의 gate로 통합했다. 배포는 Git archive 기반 pre-stage→8001 실제 평가→최종 gate→별도 8000 승격의 두 단계다. PASS 보고서도 27개 metric을 독립 검증하고, traversal·rollback 부재·image/mount/health identity 불일치를 fail-closed로 거부한다. 반복 독립 재리뷰에서 발견된 상위 집계, raw latency, provider 실행표지와 untracked build-input 우회를 모두 닫았고 focused `181 passed, 94 subtests`, 전체 Python `868 passed, 2 skipped, 240 subtests`를 통과했다.

> 2026-09-03 정형 응답 QA 갱신: provider 미설정 상태의 결정론적 재무 route가 Tool 실행 전에 `provider_unavailable`로 끝나던 결함을 수정했다. 검증된 fact, 다중 지표, 다중 기업, 기간 차이·증가율은 read-only DB에서 계산하고 claim verifier를 통과한 경우에만 답한다. 같은 공시의 여러 evidence ID는 구조화 citation에 모두 보존하고, 사용자에게 렌더링하는 접수번호 목록만 중복 제거한다. 실제 DB 8종 QA는 모두 기대 상태를 확인했고 8001 `/health`는 10초 timeout이므로 배포는 계속 차단했다.

> 위 QA와 독립 리뷰 수정을 반영한 전체 Python 회귀는 `877 passed, 2 skipped, 240 subtests`, Web은 `13 passed`다. 정확한 인계 커밋은 이 브랜치의 최신 `git log -1 --oneline`을 기준으로 한다.

> 푸시 전 독립 리뷰에서 원화 단위 표시의 부분 숫자 승인, provider timeout fallback 누락, evidence ID와 citation card 매핑 불일치 3건을 발견했다. 모두 RED 테스트로 재현한 뒤 수정했다. 원화 표시는 `조/억/만/원` 전체를 하나의 Decimal로 복원해 검증하고, 구성요소 숫자는 독립 근거로 쓰지 않는다. 설정된 provider가 실패해도 구조화 재무 route만 동일한 검증 fallback을 사용하며, 자유형/판단 route는 기존처럼 fail-closed다.

> 후속 재리뷰에서 `financial_change_reason`까지 숫자 fallback에 포함되는 범위 오류와 웹의 동일 접수번호 카드 중복을 추가로 발견했다. fallback workflow를 단일 수치·파생 계산·재무 비교·검증된 재무제표 지표로 제한했고, API evidence card는 보존하면서 웹 표시만 접수번호로 합쳤다.

> 마지막 재리뷰에서 같은 allowlist가 provider 미설정 분기에는 적용되지 않은 대칭 결함을 발견했다. provider 미설정과 provider 장애가 `_allows_deterministic_fallback` 하나를 공유하도록 합쳤고, `financial_change_reason`은 두 상태 모두 숫자 답변으로 축약되지 않는다.

> 2026-09-03 갱신: Task 5 첫 커밋은 독립 리뷰에서 거절되었고 후속 fixup에서 다섯 blocker와 Docker build-context 결함을 TDD로 수정했다. 현재 tracked 평가 결과가 ADOPTED가 아니므로 Dense는 의도적으로 비활성이고 Sparse가 안전 경로다. Task 6의 claim-level verification은 별도 미추적 작업으로 분리되어 있으며 이 Task 5 fixup에 포함하지 않는다.

## Task 5 fixup 경계

- Dense 채택은 `dense-adoption-v2`, Git에 고정된 evaluator summary SHA-256, 재계산한 semantic hash/decision, 평가 당시 runtime identity, 실제 sidecar `/health` identity가 모두 같아야 한다.
- sidecar identity는 시작 시 검증한 Dense build manifest, FAISS index, chunk metadata, model identity manifest SHA-256을 노출한다. health가 닿지 않거나 하나라도 다르면 agent는 Dense client를 만들지 않는다.
- Dense hit의 `filing_id`와 각 hydrated evidence의 실제 `filing_id`가 정확히 1:1로 같아야 한다. 같은 evidence ID가 서로 다른 filing을 선언하는 충돌도 폐기한다.
- text evidence slot의 `period_start`와 `period_end`를 Sparse/Dense 선필터에 전달한다.
- 공시 text는 NFKC, zero-width/control 제거, 공백 축약과 compact marker 비교를 통과해야 Tool 결과와 summary context에 들어간다.
- 현재 `data/derived/freeform_retrieval_summary.json`은 Dense ADOPTED 평가가 아니므로 운영 상태는 계속 Sparse다. 새 평가를 통과했을 때만 tracked summary와 코드의 trust-anchor hash를 같은 리뷰 커밋에서 갱신한다.
- `.dockerignore`는 `data` 전체를 계속 제외하되 위 tracked evaluator summary 한 파일만 agent image build context에 허용한다. 이 예외가 사라지면 배포 artifact test가 실패한다.

## Git 기준점

- 시작: `99893d4` (`agent/gross-profit-qa-fix`)
- 작업 브랜치: `agent/judge-stress-v2`
- Task 1 커밋:
  - `4e3818d feat: add dense runtime identity contract`
  - `3813d0f fix: validate dense artifact identity`
  - `736f0c2 fix: bind dense identity to immutable files`
  - `1dc6178 fix: attest dense model revision provenance`
- Task 2는 별도 `feat: add independent judge stress v2 suite` 커밋으로 끝낸다. 이 문서는 해당 커밋에 포함되며 정확한 hash는 `git log -1 --oneline`이 기준이다.
- Task 3는 별도 `feat: harden public input routing` 커밋으로 끝낸다. 이 문서는 해당 커밋에 포함되며 정확한 hash는 `git log -1 --oneline`이 기준이다.
- Task 4: `d6b3f9d feat: add bounded disclosure analysis executor`
- Task 5 최초 구현: `3748871 feat: fail closed on dense retrieval adoption`
- Task 5 review fixup은 이 인수인계와 함께 단일 `fix:` 커밋으로 끝내며 정확한 hash는 `git log -1 --oneline`이 기준이다.
- Task 6~8의 정확한 최종 hash는 `git log --oneline 39649d3..agent/judge-stress-v2`가 기준이다. Task 8은 `feat: add fail-closed release gate` 커밋으로 끝낸다.

## Task 1 완료 범위

- 메인 `[agent]` 이미지에는 NumPy를 넣지 않았고 Dense extra에만 `numpy==2.5.2`를 고정했다.
- Dense 시작 시 FAISS/metadata SHA-256, FAISS type/metric, 전체 vector L2 norm, mounted model 전체 파일 size/SHA-256을 검증한다.
- Staging builder는 `BAAI/bge-m3`의 고정 revision을 로컬 Hugging Face cache에서 `local_files_only=True`로 해석하고, staged model이 그 snapshot과 byte-identical일 때만 `model_identity.json`을 생성한다.
- `/health`와 runtime manifest에는 검증된 allowlist identity만 노출한다.
- Sparse query-time fallback은 유지했다. 기존 공개 API, 5개 Tool, credential, `.env`, DB 및 NCP 보안 설정은 변경하지 않았다.
- 과거 Docker/NCP PASS를 날짜·커밋이 있는 `PASS_HISTORICAL`로 범위 지정했다.

## Task 2 완료 범위

- 기존 300-case V1 module/script/test/config/artifacts와 856-case financial release 결과는 수정하지 않았다.
- `config/judge_stress_v2_contract.json`과 독립 V2 development builder/private validator/reporter를 추가했다.
- 총 600건은 structured 120, alias/period/correction 90, free-form 120, multi-evidence judgment 90, policy/adversarial 90, API/concurrency 60, fault 30이며 development 480/hidden holdout 120으로 나뉜다.
- tracked 코드는 development 480건만 생성한다. full 600 검증은 evaluator가 명시적으로 제공한 git-ignored private holdout 120건이 있어야 하며, 없으면 `BLOCKED_PRIVATE_HOLDOUT`, non-ignored 입력이면 오류다. actual holdout selection seed/material은 tracked contract/code에 없다.
- 실제 산출물의 development/holdout issuer, source, 독립 document, 독립 question-template-family, question-hash overlap은 모두 `0`이다. case-covered issuer group은 development `46`, holdout `12`다.
- source는 Gold 31, human-validated financial seed 8, audited financial facts 1,191, financial coverage 1, free-form 120을 hash로 고정했다. tracked development case selection은 financial facts 208, financial seed 32, free-form 168, Gold 72다. underlying audited facts는 tracked일 수 있지만 exact hidden 질문·oracle·private selection/paraphrase family는 private 입력에만 있다.
- tracked artifact는 `data/derived/judge_stress_v2_manifest.json`, JSON summary, standalone HTML summary이며 ID/hash/count/reproducibility metadata만 담는다. raw development/holdout evaluator artifact는 Git에서 무시되는 `eval/judge_stress_v2/`에만 있다. raw development의 tracked 사본은 plan 요구사항이 아니다.
- failure category는 entity, period, account, routing, retrieval, evidence, calculation, generation, security, runtime 정확히 10개다.
- `passed`는 exact bool이고 supplied failure category는 pass/fail branching 전에 검증한다. passing row의 non-null category도 fail-closed다.
- Reporter/evaluator는 manifest 자체도 fail-closed한다. 매 실행에서 정확히 600 unique row/ID, development/holdout `480/120`, 전체 및 split별 exact seven-category allocation, 각 row의 split/category, recomputed canonical `suite_sha256`을 독립 검증한 뒤에만 JSON/HTML을 쓴다.
- suite SHA-256은 `47664c91b6d241288ab4cd928955321eae301658de5681b06a40665054125a87`이다. Summary는 실제 실행 전이므로 `NOT_RUN`이다.

Privacy claim은 exact authored private 질문과 private rubric ID가 tracked 개발 코드/산출물에서 조회·재구성되지 않고 literal scan이 `0`이라는 범위다. 공개 issuer/fact provenance 또는 공개 DB fact answer를 암호학적으로 숨긴다는 주장은 아니다. hashed group/source ID는 finite public corpus와 대조 가능할 수 있으며, pre-paraphrase/public-fact reconstruction은 exact private holdout 원문과 구별한다.

## Task 3 완료 범위

- 공개 question-bearing API는 `/query`, GET `/answer`, query/evidence/answer aliases, HCX Function Calling과 공개 eval 경로 모두 OpenAPI `maxLength=2000`이며 실제 2,000/2,001 경계를 검증했다. 내부 Dense sidecar 제한은 그대로다.
- NFKC·Unicode 공백·zero-width 정규화를 공통화했다. 회사 해석은 고정 financial universe manifest와 검토된 alias config의 issuer/listed/alias/stock-code만 사용하며 대소문자를 구분하지 않는다. catalog 밖 alias는 추론하지 않고 한 글자 오타는 canonical 후보가 정확히 하나일 때만 고친다.
- 여러 회사·연도·structured metric은 독립 company × period × metric requirements로 실행한다. 잘못된 날짜, 연결/별도 충돌, 의미를 바꾸는 중복 기간은 stable clarification/error로 fail-closed한다.
- URL/percent, Base64, Unicode/spacing과 등록된 다국어 injection 표지는 최대 2,000자 bounded detection만 수행한다. decoded payload는 Tool arguments, provider prompt, public response로 전달하지 않는다. 기존 recommendation policy 우선순위와 공개 Tool 5개/필드는 유지한다.
- 확대 focused 결과는 `159 passed, 36 subtests`; 전체 Python은 `591 passed, 2 skipped, 72 warnings, 84 subtests`; Web은 `12/12`다. 실제 600건 suite, live/provider, Docker, Dense/NCP 평가는 실행하지 않았다.

재생성은 다음 명령만 사용한다. Builder는 repository의 git-ignored evaluator root 밖 raw 출력을 거부한다.

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
# 일반 개발 환경: development 480건 생성 후 BLOCKED_PRIVATE_HOLDOUT
python scripts/build_judge_stress_v2.py

# private evaluator 환경에서만 full 600 검증/manifest 생성
python scripts/build_judge_stress_v2.py `
  --private-holdout <git-ignored-private-holdout.jsonl>
python -m pytest -q tests/test_judge_stress_v2.py
```

## 최종 로컬 검증

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
$trackedTests = git ls-files 'tests/test_*.py'
python -m pytest -q $trackedTests
node --test tests/*.test.mjs
python -m compileall -q src scripts
git diff --check
```

- Task 8 focused: `181 passed, 94 subtests passed`
- Python 전체: `868 passed, 2 skipped, 86 warnings, 240 subtests passed`
- Web: `12/12 passed`
- compileall/diff: pass
- 경고는 기존 FastAPI `on_event` deprecation이다.

## 환경상 미검증·차단

- Task 7의 provider-free `ContractJudgeRuntime` run은 development root `480/480`을 검사했다. 이 runtime은 contract harness일 뿐 실제 앱/provider 정확도 평가 대상이 아니며 `runtime_release_eligible=false`다. private holdout 120개와 provider도 없어 `PARTIAL / BLOCKED_PRIVATE_HOLDOUT / BLOCKED_PROVIDER / non_release_runtime`이다.
- hidden provider 계약은 holdout free-form 24개와 multi-evidence 18개, 총 root 42개에서 versioned observation 120개를 파생한다. private 원문이 없으면 관측을 합성하거나 실행하지 않는다.
- Docker Desktop Linux engine이 없어 새 main/Dense image를 build·inspect하지 못했다.
- NCP/Dense live artifact 및 8001/8000을 변경하지 않았다.
- Dense 실제 `2,571,506` vector, model mount, 새 health identity, cold-start/restart는 `BLOCKED_ENVIRONMENT`다.
- Dense 채택 gate인 Sparse 대비 Recall@20 `+5%p`, wrong issuer/version `0`, p95 `<=2s`는 `UNVERIFIED`다. 통과 전 Sparse를 운영 안전 경로로 유지한다.
- 모델 파일은 pinned local Hugging Face snapshot과 byte-match하지만, 로컬 HF cache 자체의 진위를 검증하는 서명된 upstream manifest는 없다. 이 공급망 trust-root는 후속 보안 gate다.

## 다음 작업 — 반드시 이 순서

1. evaluator가 관리하는 git-ignored private holdout 120개를 제공한다. tracked code로 대체·재구성하지 않는다.
2. 실제 staging provider로 hidden semantic root 42개의 versioned observation 120개를 실행하고 sanitized 결과만 집계한다.
3. `python scripts/evaluate_release_candidate.py`로 실제 trust anchor를 포함한 통합 gate를 재실행한다. 현재 tracked 결과는 `BLOCKED_HARD_GATE`이므로 정상 종료 코드가 `1`이다.
4. 모든 hard gate가 통과한 경우에만 `scripts/deploy_staging.ps1`로 8001을 검증한 뒤 `scripts/deploy_release.ps1`로 정확히 같은 image를 8000에 승격한다.

각 Task는 `실패 테스트 → 최소 구현 → 관련 테스트 → 전체 회귀 → 보고서 → 독립 커밋`을 지킨다. Plan의 기준이나 read-only 제약을 낮추지 않는다.

## 중요 판단

- Compose 선언은 배포 증거가 아니다.
- NumPy는 Dense sidecar에만 둔다. 잘못된 Dense 가정은 main API 장애가 아니라 Dense 비활성화로 끝나야 한다.
- 정형 slot은 SQLite-only이고, text slot만 gate를 통과한 Hybrid를 사용할 수 있다.
- mandatory evidence slot이 없으면 결론은 `insufficient_evidence`다.
- 공개 Tool은 5개를 유지하며 내부 executor만 추가한다.
- 사용자에게는 검증 추적과 한계를 제공하되 hidden chain-of-thought는 노출하지 않는다.
- promotion은 모든 release verification과 rollback 준비 뒤에만 한다.

## Task 7 실행 계약과 현재 결과

- 실행 lane은 structured/alias/period/correction의 `deterministic_answer`, development 자유형/다중근거의 `retrieval_precheck`, `policy_guard`, hidden semantic의 `provider_answer`, `concurrency`, `fault_injection`으로 고정했다.
- deterministic/policy/error lane에서는 provider 호출을 허용하지 않는다. 오호출, evaluator error, security failure, metric 누락은 hard failure다.
- probe identity는 root case ID/hash, `judge-probes-v1`, probe kind에 결속된다. duplicate/stale/hash mismatch는 evaluator error로 거부한다.
- 메타모픽 비교는 answerability, exact finite Decimal, unit, scope, conclusion, canonical citation set을 모두 비교한다.
- provider timeout/429/5xx/malformed, Dense unavailable/malformed, restart identity는 외부 연결 없는 bounded fake adapter로 주입한다. 오류 본문은 결과에 남지 않는다.
- 20개 동시 정형 요청과 restart 전후 runtime identity를 측정한다. 결과/실패/요약/HTML에는 raw question, answer, provider body, prompt, decoded payload, secret을 쓰지 않는다.
- 2026-09-03 local run: development `480/480`, failures `0`, evaluator/security/concurrency error `0`, provider/forbidden provider call `0`. 하지만 `ContractJudgeRuntime`의 계약 하네스 결과이므로 앱 정확도 PASS가 아니고, provider p95도 미측정이다. tracked summary는 `runtime_release_eligible=false`, release state `BLOCKED`, hard-gate reasons `BLOCKED_PRIVATE_HOLDOUT`, `BLOCKED_PROVIDER`, `missing_metric:provider_p95_ms`, `non_release_runtime`, `incomplete_evaluation`이다.
- 독립 리뷰에서 typed `JudgeObservation`을 직접 만들 때 비문자 unit/scope/conclusion/citation이 mapping 검증을 우회하는 문제를 재현했다. 공통 runtime validation과 회귀 테스트로 닫았고, 수정 후 focused/전체 검증을 처음부터 다시 통과했다.

재현 명령:

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
python scripts/run_judge_stress_v2.py `
  --repository-root . `
  --manifest data/derived/judge_stress_v2_manifest.json `
  --results eval/judge_stress_v2/results.jsonl `
  --failures eval/judge_stress_v2/failures.jsonl `
  --json-summary data/derived/judge_stress_v2_summary.json `
  --html-summary data/derived/judge_stress_v2_summary.html
```

## 절대 커밋하지 말 것

`.env`, API key, PEM, NCP credential, 원본/운영 DB, FAISS/model 대용량 artifact, live mount 내용, `eval/judge_stress_v2/` raw 질문. D 드라이브 원본 DB와 live overlay/index는 계속 read-only다.

## GitHub 통합 순서

- 후속 변경은 stacked PR #3 `agent/judge-stress-v2 → agent/financial-account-catalog-v1`에 있다: https://github.com/ksm12030-sudo/mirae-asset-project/pull/3
- 팀원의 PR #2가 `agent/financial-account-catalog-v1 → main` 통합 경로다. PR #3을 먼저 병합하면 #2에 자동 포함된다. #2를 먼저 병합했다면 PR #3의 base를 `main`으로 변경한다.
- PR #3 최초 CI는 기능 실패가 아니라 test-only `numpy`/`PyYAML` 설치 누락으로 수집 단계에서 실패했다. main `[agent]` image는 계속 NumPy-free이며, CI에만 `numpy==2.5.2`, `PyYAML==6.0.3`을 명시한다.
- 리뷰·수정은 PR #3의 최신 head를 기준으로 하고, private holdout/provider 600건, 8001 staging, release hard gate와 production 승격은 별도 미완료 작업으로 유지한다.
