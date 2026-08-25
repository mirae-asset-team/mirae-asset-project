# 소스 코드

공식 공시 코퍼스의 구조 원장·검색·정정 계보·Gold 검증 코드를 저장합니다.

현재 `disclosure_db` 패키지는 다음 책임을 구현합니다.

1. 원천 manifest·XML/HTML/PDF 파싱과 결정론적 evidence ID
2. SQLite 구조 SSOT, table grid, 품질 이슈와 정정 version graph
3. 안전 lineage/PIT 필터가 적용된 FTS5 검색과 validated fact 조회
4. 원본 보존형 semantic v1 migration과 DB 감사 기록
5. 승인 Gold 계약 검증 및 evidence-addressable retrieval 평가
6. Registry 기반 Tool/Evidence 계약과 선택적 HyperCLOVA X Function Calling backend

HyperCLOVA X Function Calling은 runtime composition과 별도
`POST /v1/hcx/function-answer`에 연결돼 있습니다. Tool Evidence의 구조화된 `rcept_no`를 backend에서
검증·표시하며 유효한 접수번호가 없으면 최종 HCX 생성을 호출하지 않습니다. 실제 provider schema/Tool
Call smoke는 통과했지만 corpus DB 기반 live E2E와 NCP 배포는 아직 하지 않았습니다. Dense는 100개
`smoke_only` 계약이며 전체 embedding artifact가 없습니다.
