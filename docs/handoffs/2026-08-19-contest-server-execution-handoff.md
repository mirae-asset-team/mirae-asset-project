# 공시 에이전트 공모전 서버 실행 핸드오프

## 목표

2026-08-19 안에 D드라이브의 검증된 공시 DB를 사용하는 제출형 API를 만들고, 알려진 정확도 결함을 수정하고, 300문항 스트레스 평가와 공개 endpoint 검증까지 수행한다. 서버가 뜨는 것과 공모전 품질 gate 통과를 별도로 증명한다.

## 작업 위치

- 저장소: `C:\Users\lark0\Documents\Codex\2026-08-18\https-github-com-ksm12030-sudo-mirae\work\mirae-asset-project`
- 브랜치: `agent/disclosure-db-foundation`
- 원격: `https://github.com/ksm12030-sudo/mirae-asset-project.git`
- 설계 커밋: `a99f795 docs: design contest server MVP`
- 계획 커밋: `dd9e8d4 docs: plan contest server completion`

새 작업은 현재 브랜치에서 이어간다. 시작할 때 `git status --short --branch`와 `git log -5 --oneline`을 기록하고, 예상하지 못한 변경이 있으면 덮어쓰지 않는다.

## 반드시 읽을 문서와 순서

1. `docs/superpowers/specs/2026-08-19-contest-server-mvp-design.md`
2. `docs/superpowers/plans/2026-08-19-contest-api-deployment.md`
3. `docs/superpowers/plans/2026-08-19-contest-correctness-hardening.md`
4. `docs/superpowers/plans/2026-08-19-contest-stress-release.md`
5. `docs/development-log.md`

`docs/superpowers/specs/2026-08-19-agent-stress-evaluation-design.md`의 과거 `82e5b30` 버전은 앞부분이 잘렸다. 현재 파일은 대체 문서를 명시하는 정상 안내 문서이며, 실행 요구사항은 3번 계획 문서가 권위 원천이다.

## 검증된 데이터 자산

| 자산 | 경로 | 검증값 |
|---|---|---|
| immutable base | `D:\mirae-asset-project\db\semantic-v1_129f5b0\disclosure_corpus_semantic_v1.sqlite` | 38,773,280,768 bytes; SHA-256 `b8fb3be8b90d0cb1d8bc2491bee575aee632d29cc9bade21070e7e7b51646563` |
| fact overlay | `D:\mirae-asset-project\db\agent\agent_overlay.sqlite` | 1,028,096 bytes; quick check `ok`; imported 651 = financial 8 + event 643 |
| sparse index | `D:\mirae-asset-project\db\agent\agent_search.sqlite` | 270,479,360 bytes; quick check `ok`; search rows 329,323; trigram rows 298,128 |
| attestation | `data\derived\database_distribution_manifest_semantic_v1.json` | base hash/size/mtime와 함께 사용 |

원본과 live 파생 DB는 read-only다. rebuild는 `D:\mirae-asset-project\staging`에서 완료·검증한 뒤 recoverable rename으로 승격하고 기존 파일을 `.previous.sqlite`로 보존한다.

## 현재 코드·평가 기준

- 기존 FastAPI: `/health`, `/v1/query/plan`, `/v1/evidence/search`, `/v1/financial-facts`, `/v1/event-facts`, `/v1/calculate`, `/v1/answer`
- 새로 필요한 endpoint: 공모전 호환 `POST /query`
- 테스트 기준: 175 passed, optional 2 skipped
- 평가 실행 수: 31 audited + 83 holdout = 114; 114 failures가 아니다.
- 실행 오류 0, unsafe answer 0, false numeric claim 0
- expected answerability 89/114
- strict full pass 6/114; quality gate는 false
- Recall@20 `0.7647058824`, complete recall `0.75`, MRR `0.3449449856`
- 최근 stage p95: planner `0.41ms`, fact `172.61ms`, local retrieval `156.53ms`, end-to-end 약 `5.1s`

## 알려진 원인과 수정 우선순위

1. `2024-12-31 연결 당기순이익`을 IS duration이 아닌 instant로 계획해 6개 재무 질문이 실패한다.
2. event candidate의 composite `value_raw`를 전체 Decimal로 파싱해 숫자 cell이 존재해도 다수 reject한다.
3. q009/q010 주식수 predicate alias가 부족하다.
4. text 질문의 명시 filing date가 exact filter로 전달되지 않아 인접 날짜 공시가 먼저 나온다.
5. deterministic generator가 bundle의 최대 8개 evidence를 전부 인용한다.

이 원인은 계획 2의 Task 1–4와 직접 연결된다. Gold 답이나 gate를 바꾸지 말고 TDD로 수정한다.

## 실행 규율

- 새 대화는 먼저 `superpowers:executing-plans`를 읽고 계획 1부터 순차 실행한다.
- 각 Task는 RED test, 최소 구현, GREEN test, 관련 회귀, 독립 커밋 순서다.
- 계획의 checkbox를 실행 직후 갱신하고 `docs/development-log.md`에 명령·결과·실패 원인을 기록한다.
- 한 번에 하나의 primary cause만 고친다.
- 생성 DB, raw response, checkpoint, `.env`, log, credential은 커밋하지 않는다.
- `CLOVASTUDIO_API_KEY`가 없으면 deterministic structured fallback만 검증하고 provider gate를 미완료로 남긴다.
- PostgreSQL, OpenSearch, 전체 dense embedding은 300문항 결과가 필요성을 증명하기 전에는 시작하지 않는다.

## 첫 실행 명령

```powershell
Set-Location 'C:\Users\lark0\Documents\Codex\2026-08-18\https-github-com-ksm12030-sudo-mirae\work\mirae-asset-project'
git status --short --branch
git log -5 --oneline
python -m unittest discover -s tests -v
python -m compileall -q src scripts tests
```

그 다음 `docs/superpowers/plans/2026-08-19-contest-api-deployment.md`의 Task 1부터 실행한다. FastAPI extra 설치처럼 네트워크가 필요한 명령은 사용자 승인을 받아 수행한다.

## 즉시 중단 조건

- base DB 크기·mtime·SHA attestation 불일치
- unsafe answer 또는 false numeric claim 1건 이상
- cross-filing/unknown citation 1건 이상
- source DB나 live derived DB의 의도하지 않은 변경
- evaluator error 1건 이상
- 같은 seed/source hash의 stress manifest 불일치
- 예상하지 못한 user change와 수정 대상이 겹침

중단 시 gate를 낮추지 말고 `superpowers:systematic-debugging`으로 최소 재현부터 만든다.

## 외부 조건

최종 제출 완료에는 다음이 필요하다.

- 교체된 HyperCLOVA X credential
- NCP Server 또는 승인된 공개 endpoint
- Public IP, ACG inbound rule, 최소 80GB data volume
- 외부 네트워크에서 `/health`와 `/query` 검증
- 서버 재시작 후 readiness와 대표 질문 재검증

이 자원이 없더라도 로컬/Docker 제출형 서버, 정확도 수정, deterministic 300문항 평가까지 진행한다. 외부 조건을 수행하지 않았으면 팀 demo 가능 상태와 최종 submission ready 상태를 구분해 보고한다.

## 새 대화 시작 프롬프트

아래 프롬프트를 그대로 사용한다.

```text
이 저장소의 공시 에이전트 공모전 서버 구현을 이어서 실행해줘.
먼저 docs/handoffs/2026-08-19-contest-server-execution-handoff.md를 전부 읽고,
그 문서에 적힌 설계와 세 개 구현계획을 순서대로 실행해.
superpowers:executing-plans와 각 구현/버그 수정 시 필요한 TDD·debugging·verification skill을 반드시 적용해.
D드라이브 원본 DB와 live overlay/index는 read-only로 취급하고, 계획의 hard gate를 낮추지 마.
각 Task마다 테스트 결과와 커밋을 남기고, 외부 credential/NCP 자원이 없으면 그 부분만 명시적으로 blocked 처리하면서 로컬에서 가능한 다음 Task를 계속 진행해.
```
