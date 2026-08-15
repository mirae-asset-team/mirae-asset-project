# 소스 코드

공식 공시 코퍼스의 구조 원장·검색·정정 계보·Gold 검증 코드를 저장합니다.

현재 `disclosure_db` 패키지는 다음 책임을 구현합니다.

1. 원천 manifest·XML/HTML/PDF 파싱과 결정론적 evidence ID
2. SQLite 구조 SSOT, table grid, 품질 이슈와 정정 version graph
3. 안전 lineage/PIT 필터가 적용된 FTS5 검색과 validated fact 조회
4. 원본 보존형 semantic v1 migration과 DB 감사 기록
5. 승인 Gold 계약 검증 및 evidence-addressable retrieval 평가

HyperCLOVA X 생성기, dense retrieval, 운영 서버는 아직 연결하지 않았습니다. 이 패키지의 DB·근거
계약과 Gold 기준선을 통과한 뒤 별도 계층으로 추가합니다.
