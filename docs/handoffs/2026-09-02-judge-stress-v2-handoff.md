# Judge Stress V2 인수인계 — 2026-09-03

## 결론

`agent/judge-stress-v2` 브랜치에서 Task 1~5를 로컬 구현·검증했다. Task 2는 독립 600건 suite와 안전한 JSON/HTML report contract까지만 완료했으며 실제 앱 평가는 `NOT_RUN`이다. Task 6~8과 8001/8000 배포는 미완료다. 다음 작업은 Task 6 claim-level verification이다.

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

- Task 5 expanded focused: `237 passed, 1 skipped, 34 warnings, 31 subtests passed`
- Git 추적 Python 전체: `666 passed, 2 skipped, 86 warnings, 146 subtests passed`
- Web: `12/12 passed`
- compileall/diff/Compose static config: pass
- 문자 그대로의 `python -m pytest -q`는 별도 작업자의 미완성·미추적 Task 6 테스트 1개에서만 `11 failed`이고 나머지 `666 passed, 2 skipped`다. 위 재현 명령은 Git 추적 suite 전체를 실행해 Task 6 작업물을 명시적으로 제외한다.
- 경고는 기존 FastAPI `on_event` deprecation이다.

## 환경상 미검증·차단

- Task 2는 suite 생성만 완료했다. 600-case 앱 실행, failure triage, latency/concurrency/fault 결과는 `NOT_RUN`이다.
- Docker Desktop Linux engine이 없어 새 main/Dense image를 build·inspect하지 못했다.
- NCP/Dense live artifact 및 8001/8000을 변경하지 않았다.
- Dense 실제 `2,571,506` vector, model mount, 새 health identity, cold-start/restart는 `BLOCKED_ENVIRONMENT`다.
- Dense 채택 gate인 Sparse 대비 Recall@20 `+5%p`, wrong issuer/version `0`, p95 `<=2s`는 `UNVERIFIED`다. 통과 전 Sparse를 운영 안전 경로로 유지한다.
- 모델 파일은 pinned local Hugging Face snapshot과 byte-match하지만, 로컬 HF cache 자체의 진위를 검증하는 서명된 upstream manifest는 없다. 이 공급망 trust-root는 후속 보안 gate다.

## 다음 작업 — 반드시 이 순서

1. **Task 6:** 주장별 citation·Decimal 숫자 검증과 결정론적 fallback을 구현한다. 현재 미추적 두 파일은 Task 5 커밋과 분리한다.
2. **Task 7:** 공격·metamorphic·장애·20동시 요청 평가를 실행하고 V2 JSON/HTML을 실제 결과로 갱신한다.
3. **Task 8:** 모든 hard gate 통과 후에만 8001 staging → 동일 image 8000 승격을 수행한다.

각 Task는 `실패 테스트 → 최소 구현 → 관련 테스트 → 전체 회귀 → 보고서 → 독립 커밋`을 지킨다. Plan의 기준이나 read-only 제약을 낮추지 않는다.

## 중요 판단

- Compose 선언은 배포 증거가 아니다.
- NumPy는 Dense sidecar에만 둔다. 잘못된 Dense 가정은 main API 장애가 아니라 Dense 비활성화로 끝나야 한다.
- 정형 slot은 SQLite-only이고, text slot만 gate를 통과한 Hybrid를 사용할 수 있다.
- mandatory evidence slot이 없으면 결론은 `insufficient_evidence`다.
- 공개 Tool은 5개를 유지하며 내부 executor만 추가한다.
- 사용자에게는 검증 추적과 한계를 제공하되 hidden chain-of-thought는 노출하지 않는다.
- promotion은 모든 release verification과 rollback 준비 뒤에만 한다.

## 절대 커밋하지 말 것

`.env`, API key, PEM, NCP credential, 원본/운영 DB, FAISS/model 대용량 artifact, live mount 내용, `eval/judge_stress_v2/` raw 질문. D 드라이브 원본 DB와 live overlay/index는 계속 read-only다.
