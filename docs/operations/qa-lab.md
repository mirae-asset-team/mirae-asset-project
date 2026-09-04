# 팀 검수 데스크 (`/lab`)

공개 질문 화면(`/`)은 이용자용이며 브라우저에만 대화를 남긴다. 팀 QA 원장은
`/lab`이다. 검수 판정, Gold 후보·승인 이력, 성능 측정, 발전 과정을 서버의 쓰기
가능한 SQLite에 쌓는다. QA 판정과 Gold 정답은 서로 다른 테이블에 저장된다.

NCP 팀 주소 예: `https://mirae-qa.example.com/lab`

## 무엇을 쌓는가

- **검수 기록**: 질문, 서버 답, 사람 판정, 메모, 지연, 인용, request id
- **성능 기록**: pytest/평가 스위트 통과·실패·p50/p95, 커밋, 메모
- **발전 과정**: 무엇을 바꿨고 무엇이 아직인지 짧은 팀 로그
- **Gold Workbench**: QA 기록에서 만든 후보, 정확한 근거 패킷, 정답 계약, 자동검사,
  승인·반려 이력

판정 값은 `correct`, `partial`, `abstain_ok`, `incorrect`, `unsafe`, `error`다.

## 활성화

공개 제출 서비스 8000에는 QA DB를 설정하지 않으므로 `/lab/api/*`가 활성화되지
않는다. 로컬에서는 대표 PC만 접근하고, NCP 팀 QA는 별도 Compose의 Caddy Basic
Auth 뒤에서만 사용한다. 화면에 적는 작성자·검수자 이름은 감사용 라벨일 뿐 인증된
개인 신원이 아니다. 따라서 서로 다른 이름 검사는 실수 방지용 절차 통제이며, 실제
2인 검수 여부는 팀 운영자가 별도로 확인한다. 공시 원본 DB는 읽기 전용이고, 원장
파일만 `/runtime/qa_lab.sqlite`(로컬은 `DISCLOSURE_QA_DB`)에 쌓인다.

```powershell
$env:DISCLOSURE_QA_DB = 'D:\mirae-asset-project\runs\qa_lab.sqlite'
```

로컬에서 에이전트를 띄운 뒤 `http://127.0.0.1:8000/lab`을 연다. `질문하고 답 받기`는
같은 서버의 `POST /query`를 쓰므로 공개 화면과 같은 답을 본다.

## Gold 작업 흐름

1. `검수 기록`에서 질문하고 답을 받은 뒤 QA 판정을 저장한다.
2. 해당 행의 `Gold 후보`를 누른다. QA 기록은 그대로 보존되고 별도 후보가 생성된다.
3. `Gold Workbench`에서 모델 답을 열기 전에 근거 문장·표 셀, 공시일, 현재본 여부,
   locator를 확인한다. 원본 파일이 서버에서 접근 가능하면 `원본 파일 열기`를 쓴다.
4. 사용할 evidence와 역할을 선택하고 정답 형식, 기간, 단위, 범위, 버전 기준을 입력한다.
5. `초안 저장·재검사`로 스키마·근거 존재·출처·정정 계보를 확인한다.
6. 정답 작성자와 **다른 이름의 검수자**가 `Gold 승인`한다. 이름이 같은 자기 승인은
   차단되지만, 이름 자체는 인증 신원이 아니므로 팀 운영자가 실제 2인 검수를 확인한다.
7. 승인된 레코드만 `/lab/export/gold.jsonl`에서 canonical JSONL로 내려받는다.

승인 시 코퍼스 DB를 다시 읽어 저장된 근거 패킷이 오래된 상태가 아닌지 확인한다.
표 셀 답변은 행·열 헤더와 단위를 원문에서 대조하고 `표 구조 확인`을 체크해야 한다.
승인된 후보는 수정할 수 없다.

자동검사는 원문이 질문에 의미상 답하는지 대신 판단하지 않는다. 문맥의 충분성,
연결·별도 범위, 기간 의미, 답변 불가능 여부는 사람이 확인한다.

## NCP 팀 전용 배포

기존 8 GB 제출 서버 `mirae-contest-agent`와 공인
`http://101.79.31.221:8000` 서비스는 변경하지 않는다. 전체 BGE-M3 인덱스와 원문
DB는 32 GB 시간제 검수 서버 `mirae-dense-lab`에서 별도 Compose 프로젝트로
띄운다. 현재 `compose.team-qa.yaml`은 Caddy를 host 80/443에 바인딩하고 모든 경로에
Basic Auth를 적용한다. 검수 서버 ACG는 관리자 IP의 TCP 22와 팀 QA 도메인에 필요한
TCP 80/443만 허용한다. 외부 패키지 저장소와 CLOVA 호출에는 아웃바운드 TCP 443이
필요하다. 코드 전용 릴리스를 `/srv/mirae/app-team-qa`에 배치하고 비밀 환경 파일과
암호를 출력하지 않은 채 사용한다.

```bash
cd /srv/mirae/app-team-qa
docker compose -f compose.team-qa.yaml -p mirae-team-qa up --build -d
docker compose -f compose.team-qa.yaml -p mirae-team-qa ps
```

팀원은 팀 QA 도메인에서 Basic Auth를 통과한 뒤 접속한다.

```text
https://<DISCLOSURE_QA_HOST>/lab
```

무인증 요청이 401이고 인증된 요청만 200인지 확인한다. 아래 `<password>`는 셸
history나 문서에 남기지 않는다.

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' https://<DISCLOSURE_QA_HOST>/lab
curl -fsS -u mirae-team:<password> https://<DISCLOSURE_QA_HOST>/health
docker inspect mirae-team-qa-team-qa-1 --format '{{json .Mounts}}'
```

base·agent·attestation mount는 `rw=false`, runtime만 `rw=true`여야 한다. QA 배포는
기존 릴리스 체크리스트와 별도이며 `/lab`이 켜져도 공모전 품질 gate 통과를
의미하지 않는다.

롤백은 팀 QA 프로젝트만 내린다. 기존 공인 컨테이너와 QA SQLite 파일은 지우지
않는다.

```bash
cd /srv/mirae/app-team-qa
docker compose -f compose.team-qa.yaml -p mirae-team-qa down
```

## HTML 원장

화면의 `HTML 원장 내려받기`는 `/lab/export.html`이다. 스크립트 없는 정적 HTML이라
회의 자료나 오프라인 공유에 쓴다.

승인 Gold JSONL은 `/lab/export/gold.jsonl`이다. 파일이 비어 있으면 승인된 후보가 아직
없는 것이며, 후보나 반려 레코드는 섞이지 않는다.
