# QA Growth v4 안전 통합 및 main 승격 후보 (2026-09-05)

## 목적

팀의 최신 `agent/qa-growth-v4` QA 보완을 Judge Stress V2가 이미 가진 입력 방어, bounded analysis, 주장 단위 검증과 함께 `main`으로 가져온다. D 드라이브 원본 DB와 live overlay/index, NCP 데이터 mount는 수정하지 않는다.

## 입력과 선택 근거

- 기준 main: `cee4a5609157c358c8da4814debd1f14e96e95ea`
- QA source: `9d8c06abfd6edbf0f2533d0d18d35dbb99871eea`
- 통합 후보의 기능 수정 기준: `810ca5b6aa535241991600b61ebb296ae61785aa`
- 직접 merge는 QA source 이력에 불필요한 `.github.zip` 커밋이 포함되어 있고 Judge 브랜치와 계보가 갈라져 있어 채택하지 않았다.
- 대신 검토 가능한 QA 커밋을 main 위에 순서대로 이식하고 충돌을 기능별로 해소했다. 문제 커밋 `cbb8004`는 후보의 조상이 아니며 추적 `.pem`, `.key`, `.zip`, 실제 `.env`는 없다.

## 충돌 해소 원칙

1. Judge의 public input hardening, 5개 Tool 계약, evidence ownership, claim verification과 fail-close 정책을 우선 보존했다.
2. QA Growth의 기업명 변형, 공시유형, 파생비율, 문서형 질의, 단위·전제·금액 확인, 회계항등식, 외화·미공개정보 보류를 그 계약 안에 연결했다.
3. 회계항등식은 기존 공개 `requirements` 응답을 바꾸지 않고 내부 `metric_ids`로 실행한다.
4. 금액 확인도 일반 결정론 응답으로 바로 노출하지 않고 주장 단위 검증과 citation 매핑을 통과시킨다.
5. 증가 원인처럼 이유를 요구하는 질문은 provider 장애 때 숫자만 답하는 식으로 의미를 축소하지 않고 계속 fail-close한다.

## 독립 리뷰 수정

- 공개 8000 Compose에서 `DISCLOSURE_QA_DB`를 제거하고 API의 `/runtime` 암묵 활성화를 없앴다. 공개 질문은 계속 무로그인이지만 QA/Gold 쓰기 API는 별도 팀 QA Compose에서만 명시적으로 켠다.
- 다계정 비율과 회계항등식은 기업, 정확한 기간, 연결/별도, 공시번호가 모두 같은 validated fact만 계산한다. 하나라도 없거나 다르면 `financial_calculation_grain_mismatch`로 보류한다.
- QA 정답 인덱스 키에 `scope`를 포함해 연결과 별도 값이 서로 덮어쓰지 않게 했다.
- Ground Truth 확증은 숫자 셀 일치뿐 아니라 `financial_fact_evidence`의 검증 상태, 기업, 계정, 기간, 범위, 공시번호와 scaled value를 모두 독립 대조한다.
- 자연스러운 영어 문장의 등록된 재무계정명은 ASCII 단어 경계로 인식한다. 등록되지 않은 alias나 유사 계정은 계속 추론하지 않는다.
- 팀 QA 운영 문서를 실제 Compose의 HTTPS 도메인/Caddy Basic Auth 구성과 일치시켰다. 화면의 검수자 이름은 인증 신원이 아닌 감사 라벨임을 명시했다.

## 검증 결과

- `PYTHONPATH=src python -m pytest -q`: `936 passed, 2 skipped, 98 warnings, 260 subtests passed`
- `python -m compileall -q src scripts tests`: 통과
- `node --test tests/web_history.test.mjs tests/web_api.test.mjs`: `13 passed`
- `team-qa npm test`: `1 passed`
- 저장소·배포·Ground Truth 위생 묶음: `48 passed, 94 subtests passed`
- `git diff --check`: 통과
- tracked secret/archive 이름 검사: 실제 `.env`, `.pem`, `.key`, `.zip` 없음 (`.env.example`만 추적)
- 팀 QA `next build`: `npm ci`가 이 호스트에서 장시간 무응답이라 의존성 설치를 중단했고 `BLOCKED_ENVIRONMENT`로 남긴다. 팀 QA의 독립 단위 테스트는 통과했으며 운영 8000 API 이미지와는 별도 앱이다.

## 배포 판정

코드 통합과 로컬 회귀 통과는 Judge Stress V2 운영 승격 허가가 아니다. tracked release summary는 여전히 `BLOCKED_HARD_GATE`이며 private holdout, 실제 staging/provider 관측, 신뢰 identity 및 Recall@20 기준이 해결되지 않았다. 따라서 임계값을 완화하거나 보고서를 PASS로 바꾸지 않는다. 8000 교체는 동일-image staging과 통합 release gate가 실제 PASS한 경우에만 수행한다.

## 다음 실행

1. PR CI와 변경 리뷰를 통과시켜 이 후보를 `main`에 병합한다.
2. git-ignored private holdout과 실제 provider/staging 결과로 600건 raw 결과를 생성한다.
3. Dense/Sparse identity 및 Recall·wrong issuer/version·latency를 재측정한다.
4. 통합 release gate가 PASS하면 정확히 같은 image를 8000으로 승격하고 smoke와 rollback readiness를 기록한다.
