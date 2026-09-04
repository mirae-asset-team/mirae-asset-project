# HyperCLOVA X 최종 게이트 및 평가 API 호환 설계

## 목표

현재 결정론적 300문항 결과를 실서비스 모델 품질로 오해하지 않으면서, 교체된 HyperCLOVA X credential이 준비되는 즉시 같은 300문항을 실제 provider 경로로 실행하고 공모전 예시 API로 평가할 수 있게 한다.

## 현재 결함

- `scripts/evaluate_agent_stress.py`가 `use_hcx=False`를 고정하므로 provider 300 실행 경로가 없다.
- provider가 미구성인 실행과 provider 필수 실행이 명령 수준에서 구분되지 않는다.
- provider p95와 provider 전용 GO/NO-GO가 결과 JSON에 없다.
- 공식 과제자료의 예시 `GET /answer?question_id={id}&question={question}` 표면이 없다.

## 계약

### Provider 실행 모드

- 기본 모드는 `disabled`로 유지해 기존 재현성과 비용 0인 로컬 검증을 보존한다.
- `--provider-mode required`에서만 HyperCLOVA X를 켠다.
- required 모드에서 credential이 없으면 첫 문항 전에 종료한다. 결정론적 fallback을 provider 통과로 계산하지 않는다.
- checkpoint identity에는 provider mode와 모델명을 포함한다. 서로 다른 실행 모드의 checkpoint를 재사용하지 않는다.
- summary에는 `provider_required`, `provider_configured`, `provider_end_to_end_p95_ms`, `provider_gate_passed`, `provider_gate_reasons`를 기록한다. 키, 원문 응답, 질문 본문은 기록하지 않는다.

### Provider GO

다음이 모두 참일 때만 provider gate가 통과한다.

- required 모드이며 provider가 실제 구성됨
- 300개 모두 완료
- 기존 hard gate 통과
- 300/300 case pass
- answerability agreement 0.95 이상
- numeric exactness 1.0
- citation precision 1.0
- citation recall 0.9 이상
- end-to-end p95 10초 이하

### 공모전 예시 API

- `GET /answer`는 `question_id`, `question` query parameter를 받는다.
- 반환 필수 필드는 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`다.
- `retrieved_context`는 검증된 근거의 접수번호, 공시일, 제목, 제한된 인용만 포함한다.
- `think_trace`는 숨은 사고과정이 아니라 `질의 구조화 -> 검색 -> 정정/수치 검증 -> 근거 귀속` 같은 짧은 처리 단계 요약이다.
- 기존 `/query`, `/v1/answer`, `/health`, 웹 UI 계약은 유지한다.
- 익명 요청 제한은 `GET /answer`에도 동일하게 적용한다.

## 외부 차단 경계

교체된 credential이나 인증된 NCP 세션이 없으면 코드·테스트·문서까지만 완료한다. 실제 schema smoke, provider 300, NCP 재배포는 `BLOCKED_EXTERNAL`이며 성공으로 표시하지 않는다.
