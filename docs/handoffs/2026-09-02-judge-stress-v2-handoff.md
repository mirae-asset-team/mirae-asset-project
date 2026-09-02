# Judge Stress V2 인수인계 — 2026-09-02

## 결론

`agent/judge-stress-v2` 브랜치에서 계획 Task 1만 구현·검증했다. Task 2~8과 8001/8000 배포는 미완료이며, 완료로 간주하면 안 된다. 다음 작업은 Task 2의 독립 600건 Judge Stress V2 평가 구축이다.

## Git 기준점

- 시작: `99893d4` (`agent/gross-profit-qa-fix`)
- 작업 브랜치: `agent/judge-stress-v2`
- Task 1 커밋:
  - `4e3818d feat: add dense runtime identity contract`
  - `3813d0f fix: validate dense artifact identity`
  - `736f0c2 fix: bind dense identity to immutable files`
  - `1dc6178 fix: attest dense model revision provenance`

## 이번에 완료한 범위

- 메인 `[agent]` 이미지에는 NumPy를 넣지 않았고 Dense extra에만 `numpy==2.5.2`를 고정했다.
- Dense 시작 시 FAISS/metadata SHA-256, FAISS type/metric, 전체 vector L2 norm, mounted model 전체 파일 size/SHA-256을 검증한다.
- Staging builder는 `BAAI/bge-m3`의 고정 revision을 로컬 Hugging Face cache에서 `local_files_only=True`로 해석하고, staged model이 그 snapshot과 byte-identical일 때만 `model_identity.json`을 생성한다.
- `/health`와 runtime manifest에는 검증된 allowlist identity만 노출한다.
- Sparse query-time fallback은 유지했다. 기존 공개 API, 5개 Tool, credential, `.env`, DB 및 NCP 보안 설정은 변경하지 않았다.
- 과거 Docker/NCP PASS를 날짜·커밋이 있는 `PASS_HISTORICAL`로 범위 지정했다.

## 최종 로컬 검증

```powershell
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
python -m pytest -q tests/test_dense_runtime.py tests/test_deployment_artifacts.py
python -m pytest -q
node --test tests/web_history.test.mjs tests/web_api.test.mjs
python -m compileall -q src scripts
git diff --check
```

- Dense/deployment focused: `29 passed`
- Python 전체: `553 passed, 2 skipped, 58 warnings, 74 subtests passed`
- Web: `12/12 passed`
- compileall/diff check: pass
- 경고는 기존 FastAPI `on_event` deprecation이다.

## 환경상 미검증·차단

- Docker Desktop Linux engine이 없어 새 main/Dense image를 build·inspect하지 못했다.
- NCP/Dense live artifact 및 8001/8000을 변경하지 않았다.
- Dense 실제 `2,571,506` vector, model mount, 새 health identity, cold-start/restart는 `BLOCKED_ENVIRONMENT`다.
- Dense 채택 gate인 Sparse 대비 Recall@20 `+5%p`, wrong issuer/version `0`, p95 `<=2s`는 `UNVERIFIED`다. 통과 전 Sparse를 운영 안전 경로로 유지한다.
- 모델 파일은 pinned local Hugging Face snapshot과 byte-match하지만, 로컬 HF cache 자체의 진위를 검증하는 서명된 upstream manifest는 없다. 이 공급망 trust-root는 후속 보안 gate다.

## 다음 작업 — 반드시 이 순서

1. **Task 2:** 기존 300건·856건을 그대로 보존하고 별도 600건을 `480 development + 120 hidden holdout`으로 생성한다. 고정 배분은 정형 120, 별칭/기간/정정 90, 자유형 120, 다중근거 90, 보류/정책/공격 90, API/동시성 60, 장애주입 30이다. JSON/HTML과 10개 failure category를 만든다.
2. **Task 3:** NFKC·제로폭·manifest alias·유일한 1글자 오타·한영 회사명·종목코드·다중 요구조건 분해·공개 질문 2,000자 제한을 공통화한다.
3. **Task 4:** 평가 전용 `plan_analysis → search_analysis`를 공개 Function Calling 내부 `BoundedAnalysisExecutor`에 연결한다. 공개 Tool은 5개를 유지한다.
4. **Task 5:** issuer/공시버전/기간 선필터 후 Sparse+Dense gate를 평가한다. 기준 미달이면 Sparse로 되돌린다.
5. **Task 6:** 주장별 citation·Decimal 숫자 검증과 결정론적 fallback을 구현한다.
6. **Task 7:** 공격·metamorphic·장애·20동시 요청 평가를 실행한다.
7. **Task 8:** 모든 hard gate 통과 후에만 8001 staging → 동일 image 8000 승격을 수행한다.

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

`.env`, API key, PEM, NCP credential, 원본/운영 DB, FAISS/model 대용량 artifact, live mount 내용. D 드라이브 원본 DB와 live overlay/index는 계속 read-only다.
