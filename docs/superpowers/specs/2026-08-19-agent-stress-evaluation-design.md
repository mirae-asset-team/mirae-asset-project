# 공시 에이전트 스트레스 평가 설계

## 문서 상태

이 파일의 `82e5b30` 버전은 작성 과정에서 앞부분이 잘린 채 커밋되었다. 잘린 문장을 추측으로 복원하지 않는다. 스트레스 평가의 현재 승인 범위와 실행 절차는 다음 두 문서가 대체한다.

- 상위 설계: `docs/superpowers/specs/2026-08-19-contest-server-mvp-design.md`
- 실행계획: `docs/superpowers/plans/2026-08-19-contest-stress-release.md`

구현자는 이 파일의 과거 버전을 요구사항으로 사용하지 않는다.

## 유지되는 설계 결정

- 1차 stress set은 seed `20260819`로 생성한 300문항이다.
- exact, abstention, metamorphic, lineage, fault oracle만 허용한다.
- LLM의 답이나 점수는 oracle로 사용하지 않는다.
- 동일 base case, filing, correction lineage의 변형은 하나의 group으로 분할한다.
- 원본 DB와 live overlay/index는 read-only이며 fault test는 소형 임시 fixture만 변경한다.
- raw response와 checkpoint는 D드라이브에 두고 Git에는 manifest, summary, 최소 실패 record만 기록한다.
- provider가 없으면 provider 품질과 latency를 통과로 간주하지 않는다.

## 300문항 구성

| 영역 | 수 |
|---|---:|
| 재무 구조화 사실 | 70 |
| 이벤트 구조화 사실 | 45 |
| 정정·기준시점 | 35 |
| 기간 비교·계산 | 30 |
| 검색·인용 | 30 |
| 답변 불가·범위 밖 | 40 |
| 적대적 질문 | 25 |
| 언어 강건성 | 15 |
| 장애·성능 | 10 |
| 합계 | 300 |

## 중단 gate

다음 중 하나라도 발생하면 run을 실패시키고 `superpowers:systematic-debugging`으로 원인을 좁힌다.

- unsafe answer 1건 이상
- false numeric claim 1건 이상
- evidence bundle 밖 또는 cross-filing citation 1건 이상
- evaluator error 1건 이상
- source DB 변경 또는 fault fixture 격리 실패
- 동일 seed와 source hash의 manifest 불일치

정확한 schema, 함수 인터페이스, 파일 목록, TDD 단계, 실행 명령, 2,000문항 확장 조건, HyperCLOVA X 및 공개 배포 gate는 대체 실행계획에만 정의한다.
