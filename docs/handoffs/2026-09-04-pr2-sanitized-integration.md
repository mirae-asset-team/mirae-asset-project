# PR #2 안전 통합 기록

## 결론

`agent/financial-account-catalog-v1`의 최종 소스 트리와 검증 산출물은 `main`에 통합할 가치가 있다. 다만 PR #2를 그대로 병합하면 과거 커밋에서 추가된 루트 `.github.zip`도 `main` 이력에 포함된다. 이 파일은 로컬 가상환경 전체와 런타임 `.env`를 묶은 생성물이며, `.env`에는 비어 있지 않은 외부 API credential이 있었다.

따라서 기존 팀 브랜치의 이력을 강제로 수정하지 않고 다음 방식으로 통합한다.

1. `origin/main`에서 `integration/pr2-sanitized`를 생성한다.
2. PR #2 head `c081ca9fe1be1546d8ee4252dcda55c059ac094a`의 최종 트리를 가져온다.
3. `.github.zip`만 제외하고 나머지 트리를 유지한다.
4. 추적된 ZIP/TAR 배포 파일 안에 런타임 `.env`가 들어 있지 않은지 검사하는 회귀 테스트를 추가한다.
5. 전체 Python·Node 회귀와 GitHub CI를 통과한 안전 통합 PR만 `main`에 병합한다.

## 확인 결과

- `.github.zip`: 런타임 `.env` 포함 — 통합 제외
- `deploy-app.tar.gz`: 런타임 `.env` 및 개인키 파일 없음
- `hcx-v1-deploy.tar.gz`: 런타임 `.env` 및 개인키 파일 없음
- `hcx-web-v1-deploy.tar.gz`: 런타임 `.env` 및 개인키 파일 없음
- credential 원문은 검사 로그, 문서, 커밋 메시지에 기록하지 않음
- NCP·HCX 설정과 운영 credential은 변경하지 않음

## 후속 작업

- 기존 PR #2와 원격 브랜치는 팀원의 작업 이력을 보존하기 위해 강제 수정하거나 삭제하지 않는다.
- 운영 credential 교체 여부는 별도의 팀 보안 결정으로 남긴다.
- `agent/qa-growth-v4`의 운영 QA 개선은 PR #2와 분기된 별도 작업이므로 후속 PR에서 충돌 검토 후 통합한다.
