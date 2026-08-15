window.ROADMAP_STAGES = [
  {
    id: "evaluation", number: "01", short: "평가·EDA", color: "#123f5b",
    kicker: "Measure before optimize", title: "평가 계약과 데이터 이해",
    tagline: "무엇을 맞혔다고 볼지 먼저 정의하되, 실제 데이터 인벤토리를 본 뒤 평가셋을 고정합니다. 검색·생성·거절을 같은 점수로 뭉개지 않습니다.",
    why: "평가셋은 마지막 채점표가 아니라 모든 기술 선택의 기준입니다. 다만 데이터 구조를 보기 전에 완전히 동결하면 일반 텍스트 질문에 편향될 수 있으므로, 질문 유형 초안을 만든 뒤 인벤토리 결과로 Gold v1을 고정합니다.",
    progress: {
      tone: "hold", label: "검수 준비 완료", headline: "23개 QA 후보의 구조·근거 대조는 끝났고, 사람 승인은 아직 0건입니다.",
      summary: "19개 층화 문서를 모두 덮는 23개 QA 후보를 만들고 접수번호·SHA·evidence ID·정정 version·source coverage를 실DB와 자동 대조했습니다. 구조 오류는 0이며 candidate review는 가능하지만, 다른 사람이 원문을 확인하기 전에는 Gold로 공개하지 않습니다.",
      score: "23 / 23", scoreLabel: "자동 계약 검증 통과 후보",
      metrics: [["23건", "QA 검수 후보", "19개 문서 100% 커버"], ["0건", "계약·DB 대조 오류", "candidate_review_ready=true"], ["0건", "사람 승인 Gold", "release gate=false"], ["분리", "평가 구조", "parser·retrieval·answer·citation"]],
      done: ["공식 코퍼스 EDA와 source inventory를 전수 생성함", "질문·answerability·required evidence·계산식을 담는 평가 계약을 정의함", "23개 model-generated 후보의 content/version/source evidence를 실DB와 자동 대조함"],
      next: ["작성자와 다른 팀원이 23개 후보의 원문 지지 여부만 확인하고 승인함", "표 질문은 header·unit·grid를 사람 검수하고 parsed_unreviewed를 human_validated로 바꿈", "정정·표·계산·근거 없음 질문을 포함해 100~200문항으로 확장함"],
      example: `question_id: q021\nanswer_origin: model_generated\nreview.status: candidate\ncandidate_filing_ids: [20231214000216, 20231214000434]\nversion_evidence: original + current\nsource_evidence: SHA + fragment/table/cell counts\n\nautomation: contract/db mismatch 0\nhuman: 원문 지지 여부 확인 후 승인`,
      artifact: ["팀 공유용 DB·Gold 검수 상세서", "../team_handoff_data_db.md"]
    },
    contract: {
      input: "공식 과제 요구, 데이터 인벤토리, 문서 유형별 대표 표본",
      output: "질문·정답·필수 evidence_id·계산식·답변 가능 여부가 포함된 Gold v1",
      never: "답 문자열만 정답으로 두거나, 같은 정정 계열을 train/test 양쪽에 배치하지 않기"
    },
    flow: [
      ["요구 계약", "공식 출력과 금지사항"], ["데이터 표본", "유형·정정·표 분포"],
      ["질문 층화", "사실·표·비교·거절"], ["근거 라벨", "접수·섹션·표·셀"],
      ["교차 검수", "라벨러 불일치 해결"], ["Gold v1", "고정 split과 채점기"]
    ],
    exampleTitle: "정답 하나가 아니라 ‘판정 가능한 묶음’으로 저장",
    exampleLead: "수치 질문은 최종 숫자뿐 아니라 어떤 셀을 어떤 식으로 계산했는지까지 있어야 파서 오류와 LLM 오류를 분리할 수 있습니다.",
    exampleCaption: "gold_qa.jsonl 개념 예시",
    example: `{
  "question_id": "Q-FIN-001",
  "question": "A사의 연결 매출액 증감률은?",
  "intent": "period_comparison",
  "answerable": true,
  "required_evidence_ids": ["E_CUR_REVENUE", "E_PREV_REVENUE"],
  "formula": "(current - previous) / abs(previous) * 100",
  "rounding": "소수점 둘째 자리",
  "risk_tags": ["unit", "period", "consolidated", "correction"]
}`,
    candidates: [
      { type: "base", badge: "기본안", name: "사람 이중 검수 Gold", role: "평가의 정본", strength: "셀 좌표·단위·정정 효력처럼 모델이 놓치기 쉬운 경계를 사람이 확인합니다.", risk: "작성 비용과 라벨러 편차가 큽니다.", rule: "핵심 test와 위험 질문에 사용" },
      { type: "challenger", badge: "보조안", name: "LLM 초안 + 사람 승인", role: "질문·오답 후보 생성", strength: "유형별 질문 초안과 distractor를 빠르게 늘릴 수 있습니다.", risk: "모델의 오류를 Gold에 복제할 수 있습니다.", rule: "자동 생성 결과는 승인 전 Gold 금지" },
      { type: "challenger", badge: "보조안", name: "템플릿 합성 QA", role: "대량 회귀셋", strength: "표 셀·계산식에서 정답을 코드로 만들 수 있어 재현성이 높습니다.", risk: "자연어 다양성과 실제 복합질문을 과소대표합니다.", rule: "사람 질문셋과 별도 점수로 유지" },
      { type: "control", badge: "대조군", name: "LLM-as-judge", role: "빠른 개발 회귀", strength: "장문 품질을 빠르게 선별할 수 있습니다.", risk: "판정 모델의 편향과 자기일관성 오류가 있습니다.", rule: "인용·숫자 정답 판정에는 단독 사용 금지" }
    ],
    criteria: [
      ["유형 커버리지", "단일 사실·표·비교·정정·PIT·복수 문서·거절·공격 질문이 모두 있는지 봅니다."],
      ["근거 완전성", "모든 검증 가능한 claim에 셀 또는 span 단위 정답 근거가 연결됐는지 측정합니다."],
      ["라벨 합의율", "두 검수자의 answerability·evidence_id 합의와 조정 사유를 기록합니다."],
      ["누출 방지", "동일 기업·문서·정정 계열·시점 템플릿이 split을 가로지르지 않는지 검사합니다."],
      ["코드 채점 가능성", "숫자·접수번호·근거 ID·거절은 결정론적 채점기로 재현돼야 합니다."],
      ["오류 진단력", "오답을 parser/retrieval/version/unit/generation/verification으로 분해할 수 있어야 합니다."]
    ],
    measurementNote: "목표 임계값은 Gold v1 분포를 확인한 뒤 정합니다. 먼저 숫자를 임의로 고정하면 쉬운 유형 비중에 따라 지표가 왜곡됩니다.",
    recommendation: {
      base: "사람 이중 검수 핵심셋 + 코드 생성 수치 회귀셋 + 별도 공격·거절셋",
      challenger: "LLM이 질문·distractor 초안을 만들고 사람이 evidence_id까지 승인하는 확장 경로",
      reason: "사람 검수는 근거 신뢰성을, 합성셋은 반복성과 규모를 담당해 서로의 약점을 보완합니다.",
      promote: "새 질문이 기존 유형 분포를 깨지 않고, 근거 합의율과 채점기 통과율을 만족할 때만 Gold에 편입"
    },
    failures: [
      "답 문자열만 저장해 접수번호가 틀린 정답을 통과시키는 문제 → evidence_id를 필수 필드로 둡니다.",
      "최신 정정본을 과거 시점 질문의 정답으로 쓰는 누출 → 모든 질문에 as_of 정책을 저장합니다.",
      "합성 질문이 특정 문장 틀만 반복 → 사람 작성 질문과 템플릿 합성 점수를 분리합니다.",
      "검색 개선과 생성 개선이 한 점수에 섞임 → retrieval과 answer 지표를 별도 리포트로 냅니다."
    ],
    deliverables: ["dataset_inventory.parquet", "gold_qa_v1.jsonl", "evidence_labels.jsonl", "split_manifest.json", "deterministic_scorer"],
    gates: ["모든 Gold 질문에 질문 유형과 answerability가 있음", "답변 가능 질문에 필수 evidence_id가 있음", "수치 질문에 단위·기간·수식·반올림 규칙이 있음", "정정/PIT/거절 질문이 별도 층으로 존재"],
    sources: [
      ["프로젝트 EDA 보고서", "../dataset_eda_report.md"], ["프로젝트 LLM 평가 설계", "../llm_system_principles.md#11-평가-설계"],
      ["RAGAS metrics", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/"], ["MLflow GenAI evaluation", "https://mlflow.org/docs/latest/genai/eval-monitor/index.html"]
    ]
  },
  {
    id: "data", number: "02", short: "데이터·파싱", color: "#07829a",
    kicker: "Lossless before convenient", title: "원본·파싱·정정 계보",
    tagline: "원본 바이트를 고정하고 XML·HTML·PDF를 구조 보존형으로 파싱합니다. 접수번호보다 더 작은 근거 주소를 이 단계에서 결정합니다.",
    why: "공시는 일반 텍스트가 아니라 섹션·표·셀·단위·주석과 시간 효력을 가진 기록입니다. 여기서 좌표를 잃으면 검색과 LLM이 나중에 정확한 인용을 복원할 수 없습니다.",
    progress: {
      tone: "hold", label: "구조 완료 · 의미 미평가", headline: "4,204건을 전수 적재했고 구조 invariant 검사는 통과했습니다.",
      summary: "원본 파일과 SHA를 보존하며 XML·HTML·PDF를 fragment·table·cell·lineage로 변환했습니다. 구조적 무결성은 통과했지만 header/unit 의미, exact span, PDF 표와 정정 계보는 사람 검수 전입니다.",
      score: "4,622 / 4,622", scoreLabel: "source 전수 처리",
      metrics: [["4,204", "filing", "manifest 접수건"], ["4,622", "source", "XML·HTML·PDF"], ["36.70M", "table cell", "물리 anchor 셀"], ["546", "unresolved", "정정 계보 보강 대상"]],
      done: ["DART XML 3,147·거래소 HTML 1,469·PDF 3·viewer HTML 3개를 판별함", "fragment 8,437,771건과 table 1,556,755건을 evidence ID로 연결함", "parse failed 0, FK 위반 0, evidence 충돌 0을 전수 validator에서 확인함"],
      next: ["text_raw와 exact source span 계약을 확정하고 필요하면 parser version을 올림", "복잡한 병합표의 header path와 unit을 층화 Gold로 검수함", "unresolved 546건과 PDF 3건을 답변 허용 전에 닫거나 명시적으로 차단함"],
      example: `source_id: src_9e1563962123a652a26a7f5c\nfiling_id: 20230102000160\nrole: main\ndetected_format: dart_xml\nsha256: 28db7e5e...fbdfe07\nparse_status: success\nstrict_xml_ok: true\ncoverage:\n  fragments: 159\n  tables: 20\n  cells: 772\n  fact_candidates: 53\n\n주의: fact_candidates는 검증된 재무 정답이 아님`,
      artifact: ["파싱·lineage 독립 검수 상세서", "../team_handoff_data_db.md#15-db-독립-검수-상세서"]
    },
    contract: {
      input: "제공 원본 파일, manifest 행, 파일별 실제 포맷과 해시",
      output: "Filing·SourceDocument·Fragment·Table·Fact·Lineage와 불변 evidence_id",
      never: "raw 수정, 표를 Markdown 한 덩어리로만 저장, 정정본과 원본을 같은 chunk로 합치기"
    },
    flow: [
      ["Hash & sniff", "바이트·포맷 고정"], ["Role resolve", "main·첨부·감사보고"],
      ["구조 파싱", "섹션·문단·표·셀"], ["의미 정규화", "단위·기간·연결범위"],
      ["Lineage", "원본·정정·유효기간"], ["Validation", "coverage·좌표 재현"]
    ],
    exampleTitle: "목표 구조와 현재 구현을 구분합니다",
    exampleLead: "현재는 원본 위치·표 grid·header path·unit 후보를 보존합니다. 아래의 value_numeric·period·scope·supersedes는 다음 canonical fact 단계의 목표이며 아직 전수 구현 완료가 아닙니다.",
    exampleCaption: "Canonical Fact 목표 계약 · 미완료 필드는 명시",
    example: `evidence_id: ev1_<filing+source_sha+locator digest>
filing_id: 20250318001234
section_path: ["III. 재무에 관한 사항", "연결재무제표"]
table_id: tbl-17
row_header_path: ["매출액"]
column_header_path: ["당기", "누적"]
value_raw: "1,234"

현재 구현:
- 물리 셀·rowspan·colspan·header path·unit_text 후보
- filing event/version과 resolved/unresolved 상태

다음 canonical validator:
- value_numeric / currency / scale
- period_start / period_end / duration basis
- consolidated / separate scope
- validated fact와 candidate fact의 엄격한 분리`,
    candidates: [
      { type: "base", badge: "기본안", name: "전용 XML/HTML 파서", role: "주요 원문 구조 추출", strength: "DART 태그·표 병합·정정 헤더 규칙을 직접 통제하고 원문 좌표를 보존합니다.", risk: "템플릿 변형별 규칙과 회귀 테스트가 필요합니다.", rule: "주요 corpus 포맷의 기본 경로" },
      { type: "challenger", badge: "PDF 후보", name: "PyMuPDF", role: "PDF 텍스트·페이지·표 후보", strength: "페이지 좌표와 텍스트 블록 접근이 가능하고 표 탐지 API를 제공합니다.", risk: "스캔 PDF와 복잡한 병합셀은 별도 검증이 필요합니다.", rule: "텍스트형 PDF baseline" },
      { type: "challenger", badge: "PDF 후보", name: "pdfplumber", role: "문자·선 기반 표 추출", strength: "페이지 객체와 table setting을 세밀하게 조정할 수 있습니다.", risk: "문서별 튜닝이 늘어날 수 있습니다.", rule: "PyMuPDF 실패 표본의 대조 후보" },
      { type: "challenger", badge: "문서 후보", name: "Docling", role: "구조화 문서·표 변환", strength: "표를 DataFrame·HTML 등으로 내보내는 고수준 파이프라인을 제공합니다.", risk: "DART 고유 XML 계보와 정확한 원본 locator는 별도 구현해야 합니다.", rule: "PDF/혼합 문서 보조 파서로 ablation" },
      { type: "control", badge: "대조군", name: "LLM 직접 파싱", role: "비정형 예외 해석", strength: "깨진 레이아웃의 의미 후보를 제안할 수 있습니다.", risk: "숫자·좌표를 생성하거나 누락할 수 있어 결정론이 깨집니다.", rule: "값 확정 금지, 경고·후보 생성에만 사용" }
    ],
    criteria: [
      ["구조 보존율", "section path·table grid·rowspan/colspan·주석·페이지 좌표가 원문과 일치하는 비율"],
      ["셀 정확도", "값뿐 아니라 행/열 헤더·단위·기간·연결/별도가 모두 맞는 exact match"],
      ["근거 재현율", "evidence_id에서 원문 셀 또는 span을 다시 열어 같은 내용을 확인할 수 있는 비율"],
      ["계보 정확도", "원본-정정 parent와 as-of 유효 버전을 수작업 chain 정답과 비교"],
      ["Coverage", "문서·섹션·표·셀 중 성공/partial/실패 비율을 포맷별로 기록"],
      ["회귀 안정성", "파서 버전 변경 후 기존 Gold locator와 값이 의도 없이 변하지 않는지 검사"]
    ],
    measurementNote: "단순 텍스트 추출량이 많다고 좋은 파서가 아닙니다. 표 헤더나 locator를 잃은 텍스트는 이 과제에서 근거로 승격하지 않습니다.",
    recommendation: {
      base: "전용 XML/HTML 구조 파서 + PyMuPDF PDF baseline + 공통 canonical adapter + immutable raw/hash inventory",
      challenger: "pdfplumber와 Docling을 동일 PDF Gold에서 비교하고, 각 문서에 가장 잘 나온 파서를 임의 선택하지 않고 포맷 규칙으로 라우팅",
      reason: "주요 XML은 도메인 규칙으로 정밀하게, PDF는 검증된 범용 엔진으로 처리하되 모든 출력은 같은 evidence 계약으로 통합합니다.",
      promote: "셀 exact match와 locator 재현율이 기본 파서보다 높고 처리시간·실패 로그까지 재현될 때 후보를 기본 경로로 승격"
    },
    failures: [
      "표 병합셀이 풀리면서 전기 값이 당기 열에 붙음 → grid와 rowspan/colspan을 보존합니다.",
      "단위 ‘백만원’이 표 밖 문장에 있어 값만 추출됨 → table unit_text를 셀 fact에 전파합니다.",
      "정정 제목 접두사만 지우고 원본과 연결했다고 간주 → 정정 헤더의 명시 참조와 confidence를 저장합니다.",
      "정정본의 같은 셀 좌표가 기존 evidence_id를 덮음 → source hash가 달라 새 ID를 만들고 supersedes와 valid_from/valid_to로 효력을 연결합니다.",
      "PDF viewer shell을 본문으로 오인 → 실제 바이트 포맷 sniff와 텍스트 coverage를 분리합니다.",
      "빈칸·대시를 0으로 변환 → not_reported/not_applicable/unknown을 구분합니다."
    ],
    deliverables: ["immutable inventory + SHA-256", "canonical schema v1", "parser adapters", "evidence registry", "version lineage graph", "parser regression report"],
    gates: ["모든 파생 레코드가 source_sha256과 parser_version을 가짐", "Gold 표본의 evidence_id가 원문으로 역참조됨", "정정 chain에 supersedes·valid_from/valid_to·resolved/unresolved 상태가 있음", "정정 전후 같은 좌표도 source hash가 다르면 별도 evidence_id를 가짐", "단위·기간·scope 없는 숫자를 계산 입력으로 승격하지 않음"],
    sources: [
      ["프로젝트 EDA 스키마", "../dataset_eda_report.md#11-권장-정규화-스키마"], ["공시 데이터 해석 원리", "../disclosure_data_principles.md#4-canonical-데이터-모델"],
      ["Docling table export", "https://docling-project.github.io/docling/_generated/examples/export_tables/"], ["PyMuPDF table extraction", "https://pymupdf.readthedocs.io/en/latest/faq.html#how-to-extract-table-content-from-documents"],
      ["pdfplumber", "https://github.com/jsvine/pdfplumber"]
    ]
  },
  {
    id: "database", number: "03", short: "DB·인덱스", color: "#2ead8e",
    kicker: "One truth, multiple read models", title: "DB와 검색 인덱스 구축",
    tagline: "현재 38GB SQLite 기준 원장과 FTS baseline까지 만들었습니다. 다음은 의미 검수 결과를 반영하고, 동등성 검사를 갖춘 뒤 운영 PostgreSQL과 Evidence API로 이관하는 일입니다.",
    why: "DB의 목적은 LLM이 직접 읽게 하는 것이 아니라 올바른 버전과 근거를 빠르고 재현 가능하게 꺼내는 것입니다. 현재 SQLite는 전수 재현·감사에 적합한 기준 산출물이고, PostgreSQL은 아직 SQLite와 동등하지 않은 운영 후보 DDL 초안입니다.",
    progress: {
      tone: "current", label: "지금 작업할 단계", headline: "기준 DB는 완성됐고, 사용자 질의용 DB는 아직 아닙니다.",
      summary: "SQLite에 전수 구조와 FTS5를 적재했습니다. 구조 Gate, 검색 smoke, 검색 관련성, 의미·답변 Gate를 코드에서 분리했고 candidate fact와 unresolved 정정본은 기본 답변 조회에서 제외합니다.",
      score: "38.48 GB", scoreLabel: "SQLite 기준 DB",
      metrics: [["8.44M", "FTS 행", "현 구조 SSOT 기준"], ["19", "회귀 테스트", "안전·Gold·blind review·FTS 포함"], ["63–116ms", "안전 회사 검색", "3질의·3회 중앙값·limit 10"], ["미평가", "검색·의미 Gate", "사람 Gold 전"]],
      done: ["candidate fact를 반환하지 않는 validated fact 조회를 구현함", "unresolved·missing_original·폐기 version을 기본 검색에서 차단하고 as-of 필터를 추가함", "다음 적재용 financial_fact grain·FTS external-content·전 fragment lineage 계약과 제출인 검색을 구현함"],
      next: ["현재 원장에서 Gold 후보 19건을 사람이 승인함", "거래소 정정 494건부터 false link 0을 목표로 계보 규칙을 검수함", "다음 index rebuild에서 FTS rowid reconciliation 후 Gold 기반 retrieval ablation을 시작함"],
      example: `안전 조회 실제 예시\nquery: "계약금액"\ncompany: "삼성전자"\ninclude_unsafe: false\nlimit: 10\nlatency median: 63.370 ms  # warm-up 1회 뒤 3회 중앙값, p95 아님\ntop_filing_id: 20241118000328\nlineage_status: resolved\nis_current: true\n\n실행 계약\n✓ filing 층에서 회사·안전 lineage 먼저 확정\n✓ 해당 filing의 FTS rowid 범위만 검색\n✓ 결과 source parse_status 재검사\n\n기본 차단\n✓ candidate fact 반환 금지\n✓ unresolved / missing_original 제외\n✓ 폐기 version 제외 또는 as_of 유효본만\n\n아직 미평가\n✗ Gold 검색 관련성\n✗ financial_fact 내용 정확도\n✗ header·unit·PDF 표 의미\n✗ Evidence API / PostgreSQL / dense RAG`,
      artifact: ["DB 구축·대안·검수 SQL 전체 보기", "../team_handoff_data_db.md#15-db-독립-검수-상세서"]
    },
    contract: {
      input: "구조 검증된 Filing·Fragment·Table·Cell·Fact candidate·Lineage와 parser revision",
      output: "현재 SQLite 기준 원장과 FTS baseline, 다음 단계에서 PostgreSQL 원장·Evidence API·검색 read model",
      never: "벡터 DB를 정본으로 사용하거나, 인덱스에만 존재하는 근거를 최종 답에 인용하기"
    },
    flow: [
      ["Canonical load", "PK·FK·제약 검사"], ["Revision pin", "corpus·schema·parser"],
      ["Read model", "검색용 parent/child chunk"], ["Lexical index", "Nori·필드 boost"],
      ["Dense option", "pgvector/Qdrant 후보"], ["Reconcile", "ID·건수·hash 대조"]
    ],
    exampleTitle: "현재 원장과 목표 운영 구조를 분리합니다",
    exampleLead: "지금 정본 산출물은 SQLite입니다. PostgreSQL과 검색 인덱스는 검수된 SQLite·원본에서 재생성되고 행 수와 hash가 대조될 때만 운영 원장으로 승격합니다.",
    exampleCaption: "현재 구현 → 다음 운영 구조",
    example: `현재: SQLite 기준 원장
filing(filing_id PK, issuer_id, filed_at, lineage_status, ...)
source_document(source_id PK, filing_id FK, sha256, ...)
fragment(evidence_id PK, source_id FK, section_path, locator, text, ...)
table_record / table_cell
fact(candidate) / fact_evidence
filing_event / filing_version
quality_issue / pipeline_run / fragment_fts

다음: PostgreSQL parity + rebuildable search document
{
  "evidence_id": "...", "parent_filing_id": "...",
  "issuer_id": "...", "filed_at": "...", "effective_version_id": "...",
  "doc_type": "periodic", "section_path": [...],
  "text": "...", "table_headers": [...], "unit": "KRW"
}`,
    candidates: [
      { type: "base", badge: "운영 후보", name: "PostgreSQL", role: "메타·계보·fact·인용 registry", strength: "트랜잭션·제약·조인·JSONB를 함께 제공해 version/evidence 무결성을 강제할 수 있습니다.", risk: "현재 DDL은 pipeline_run·fact·quality_issue와 일부 감사 열이 빠진 초안입니다.", rule: "SQLite parity와 migration 대조가 통과한 뒤 정본으로 승격" },
      { type: "base", badge: "현재 정본", name: "SQLite + FTS5", role: "전수 감사·재현·검색 sanity baseline", strength: "단일 파일로 4,204건의 관계·FK·FTS를 동일 산출물에서 검증했습니다.", risk: "다중 사용자 serving과 한국어 형태소 검색의 최종안이 아닙니다.", rule: "운영 migration 완료 전 기준 산출물" },
      { type: "base", badge: "검색", name: "OpenSearch + Nori", role: "한국어 BM25·필드검색", strength: "역색인, 필드 boost, highlight, 한국어 분석기 플러그인을 제공합니다.", risk: "운영 서비스가 하나 늘고 인덱스 동기화가 필요합니다.", rule: "lexical baseline이 메모리 엔진 한계를 보이면 기본" },
      { type: "base", badge: "분석", name: "Parquet + DuckDB", role: "EDA·품질검사·실험 집계", strength: "열 기반 파일을 직접 질의해 대량 집계와 실험 재현이 간단합니다.", risk: "동시 쓰기·온라인 트랜잭션 정본에는 맞지 않습니다.", rule: "오프라인 분석 경로로만 사용" },
      { type: "challenger", badge: "벡터 후보", name: "pgvector", role: "PostgreSQL 안 dense 검색", strength: "메타 필터와 벡터를 한 DB에서 시작해 운영 복잡도를 낮출 수 있습니다.", risk: "규모·필터·인덱스 설정에 따른 성능은 corpus에서 실측해야 합니다.", rule: "dense 이득이 있고 단일 DB가 요구량을 만족할 때" },
      { type: "challenger", badge: "벡터 후보", name: "Qdrant", role: "전용 dense/sparse/multivector 검색", strength: "payload filter, hybrid query와 RRF 등 벡터 중심 기능이 명시돼 있습니다.", risk: "정본·lexical 엔진과 별도 서비스가 추가됩니다.", rule: "pgvector가 recall/latency/필터 요구를 못 맞출 때" },
      { type: "control", badge: "단순 대조", name: "PostgreSQL FTS만", role: "최소 운영 baseline", strength: "서비스 수가 적어 배포와 백업이 가장 단순합니다.", risk: "한국어 형태소·검색 분석 기능이 목표 recall에 부족할 수 있습니다.", rule: "작은 corpus에서 반드시 비교할 대조군" }
    ],
    criteria: [
      ["정합성", "filing·fragment·fact·lineage FK 위반 0건, evidence_id orphan 0건"],
      ["재구축성", "빈 인덱스에서 corpus revision 하나로 동일 건수·hash의 read model을 다시 만드는지"],
      ["필터 정확도", "기업·기간·유형·as-of 조건이 검색 전에 정확히 적용되는지"],
      ["검색 품질", "동일 Gold에서 Recall@k·MRR·evidence-set recall 비교"],
      ["운영 비용", "RAM·디스크·index build 시간·백업/복구·서비스 수를 함께 기록"],
      ["지연", "cold/warm p50·p95와 동시 요청 시 tail latency를 분리 측정"]
    ],
    measurementNote: "DB 후보는 기능 체크리스트로 우승하지 않습니다. 동일한 chunk·embedding·필터·top-k를 사용해 품질과 운영비를 함께 비교해야 합니다.",
    recommendation: {
      base: "현재 SQLite 구조 SSOT 동결 → 안전 조회 → QA 후보 23건 원문 승인 → financial_fact → 정정·FTS 검수 → lexical baseline",
      challenger: "Gold에서 dense가 Recall@k를 실제 개선할 때 pgvector를 먼저 붙이고, 규모·multivector·필터 성능이 부족할 때만 OpenSearch/Qdrant와 비교",
      reason: "이미 검증된 38GB 기준 산출물을 버리지 않고, 의미 오류를 먼저 측정한 뒤 운영성과 검색 성능을 한 계층씩 추가합니다.",
      promote: "후보가 같은 Gold에서 품질을 개선하고 p95·메모리·재구축 복잡성의 허용 범위를 만족할 때만 기본 스택에 추가"
    },
    failures: [
      "OpenSearch 문서는 최신인데 PostgreSQL lineage는 이전 revision → 모든 인덱스에 corpus_revision을 저장하고 혼합 요청을 거절합니다.",
      "Qdrant payload만 남아 원문 locator를 잃음 → evidence registry 정본은 PostgreSQL에 유지합니다.",
      "분석용 DuckDB 파일을 API가 직접 수정 → DuckDB/Parquet는 immutable snapshot과 평가 전용으로 둡니다.",
      "하나의 긴 문서를 한 vector로 저장 → parent filing과 child fragment를 분리합니다.",
      "DB 기능이 많다는 이유로 후보 채택 → 품질 Δ와 운영비를 같은 실험표에 기록합니다."
    ],
    deliverables: ["ERD + migration", "canonical load job", "index mapping/settings", "rebuild manifest", "DB reconciliation test", "storage benchmark report"],
    gates: ["정본 FK와 evidence orphan 검사 통과", "원본에서 모든 인덱스를 재구축 가능", "검색 결과가 유효 evidence_id만 반환", "후보별 동일 데이터 스냅샷과 설정이 기록됨"],
    sources: [
      ["PostgreSQL JSONB", "https://www.postgresql.org/docs/current/datatype-json.html"], ["PostgreSQL Full Text Search", "https://www.postgresql.org/docs/current/textsearch.html"],
      ["OpenSearch search", "https://docs.opensearch.org/latest/search-plugins/"], ["OpenSearch Nori plugin", "https://docs.opensearch.org/latest/install-and-configure/additional-plugins/index/"],
      ["pgvector", "https://github.com/pgvector/pgvector"], ["Qdrant hybrid queries", "https://qdrant.tech/documentation/search/hybrid-queries/"],
      ["DuckDB Parquet", "https://duckdb.org/docs/stable/data/parquet/overview"]
    ]
  },
  {
    id: "retrieval", number: "04", short: "질의·검색", color: "#9dbc45",
    kicker: "Resolve before retrieve", title: "질의 해석과 근거 검색",
    tagline: "질문을 기업·시점·문서·지표·연산 슬롯으로 바꾸고, 유효 버전만 남긴 뒤 lexical·dense·reranker를 단계적으로 비교합니다.",
    why: "공시에서 검색 실패는 표현 유사도만의 문제가 아닙니다. 다른 회사, 다른 기간, 정정 전 문서를 잘 찾는 것이 더 위험합니다. 따라서 metadata와 version resolver가 검색 점수보다 앞에 있어야 합니다.",
    contract: {
      input: "사용자 질문, 질문 기준시점, 기업 alias registry, version graph, 검색 read model",
      output: "질문 하위요건별로 고정된 ranked evidence 후보와 retrieval trace",
      never: "as-of 해석 전 전체 corpus 검색, 서로 다른 검색 점수의 raw weighted sum, top score 하나로 answerability 결정"
    },
    flow: [
      ["Query slots", "기업·기간·유형·계정"], ["Clarify", "모호성·범위 밖"],
      ["PIT resolve", "유효 version만"], ["Parallel retrieve", "BM25 + dense"],
      ["RRF", "rank 기반 결합"], ["Rerank & bundle", "하위요건별 근거"]
    ],
    exampleTitle: "질문 하나를 검색어 하나로 보내지 않습니다",
    exampleLead: "복합 질문은 필요한 증거 집합을 먼저 나눕니다. 각 하위질문은 같은 기업·시점 계약을 공유하지만 별도 retrieval trace를 가집니다.",
    exampleCaption: "질의 계획 예시",
    example: `질문: "A사의 최근 정정 후 계약금액과 직전 공시 대비 변화는?"

QueryPlan
  entity      = issuer_id:A
  as_of       = corpus_cutoff
  intent      = correction_diff
  subqueries  = [
    {need: "effective corrected amount", doc_type: "supply_contract"},
    {need: "previous amount", relation: "parent_version"}
  ]

실행
  metadata hard filter → version resolver
  → BM25 top-N + dense top-N
  → RRF candidate merge
  → reranker
  → evidence-set completeness check`,
    candidates: [
      { type: "base", badge: "기준선", name: "Metadata filter + BM25", role: "exact token 중심 검색", strength: "기업명·접수번호·계정명·보고서명·숫자처럼 공시의 희귀 토큰에 강하고 설명 가능합니다.", risk: "동의어와 표현 차이에 약합니다.", rule: "모든 실험의 고정 baseline" },
      { type: "challenger", badge: "도전자", name: "Dense dual encoder", role: "의미 유사 검색", strength: "질문과 본문의 어휘가 달라도 의미적으로 가까운 근거를 회수할 수 있습니다.", risk: "비슷하지만 다른 기간·계정을 끌어올 수 있습니다.", rule: "BM25가 놓친 관련 근거를 실제로 회복할 때" },
      { type: "challenger", badge: "조합", name: "BM25 + Dense + RRF", role: "후보 통합", strength: "점수 척도가 다른 검색 결과를 순위만으로 합쳐 raw score calibration 문제를 줄입니다.", risk: "나쁜 dense leg가 후보 노이즈를 늘릴 수 있습니다.", rule: "각 leg 단독 성능을 먼저 확인한 뒤 적용" },
      { type: "challenger", badge: "정밀화", name: "Cross-encoder reranker", role: "상위 후보 재정렬", strength: "질문과 문서를 함께 읽어 dual encoder보다 세밀한 상호작용을 볼 수 있습니다.", risk: "후보 수에 비례해 지연과 비용이 늘어납니다.", rule: "Recall을 유지하며 top precision/MRR을 개선할 때" },
      { type: "challenger", badge: "절충", name: "ColBERT late interaction", role: "토큰 수준 재검색·재정렬", strength: "문서 토큰을 미리 계산하고 MaxSim으로 세밀한 일치를 봅니다.", risk: "인덱스 크기와 운영 복잡도가 증가합니다.", rule: "cross-encoder 지연이 크고 dense보다 정밀도가 필요할 때" },
      { type: "control", badge: "대조군", name: "긴 문맥 일괄 입력", role: "검색 없는 비교", strength: "구현이 단순하고 oracle에 가까운 문서가 작을 때 유용합니다.", risk: "문맥 중간 정보 손실과 인용 혼선이 있으며 corpus 전체에는 확장되지 않습니다.", rule: "검색 필요성을 검증하는 대조군으로만 사용" }
    ],
    criteria: [
      ["Document Recall@k", "정답 공시가 top-k 후보에 포함되는지 측정합니다."],
      ["Evidence Recall@k", "정답 section/table/cell fragment가 후보에 들어오는지 별도 측정합니다."],
      ["Evidence-set recall", "복합질문에 필요한 모든 하위근거를 함께 찾았는지 봅니다."],
      ["MRR / nDCG", "첫 정답 위치와 등급별 관련성 순위를 평가합니다."],
      ["Version accuracy", "as-of에 맞는 정정본·원본을 선택하고 폐기 버전을 배제했는지 측정합니다."],
      ["Latency / cost", "query parse, 각 retriever, fusion, reranker의 p50/p95를 따로 기록합니다."]
    ],
    measurementNote: "같은 chunk와 filter를 사용하지 않으면 검색기 비교가 아닙니다. 각 leg를 단독 평가하고, hybrid의 이득이 어느 leg에서 왔는지 남깁니다.",
    recommendation: {
      base: "규칙+HCX 보조 질의 슬롯화 → metadata/PIT hard filter → BM25 → 하위질문별 evidence bundle",
      challenger: "BM25와 dense를 병렬 실행해 RRF로 합치고, top-N에만 도메인 reranker 적용",
      reason: "공시 exact token의 강점을 잃지 않으면서 의미적 표현 차이만 dense가 보완하고, 비싼 reranking 범위를 제한합니다.",
      promote: "전체·유형별 Recall@k 또는 MRR이 개선되고 version accuracy를 훼손하지 않으며 p95 한도 안에 있을 때 한 층씩 승격"
    },
    failures: [
      "기업 alias 해소 실패로 동명이인 문서 검색 → issuer_id를 확정하지 못하면 clarification_required로 보냅니다.",
      "정정 전 문서가 lexical 점수로 1위 → version resolver 결과 밖 문서는 후보에 넣지 않습니다.",
      "dense와 BM25 raw score를 단순 가중합 → RRF baseline 후 validation set에서만 가중치를 조정합니다.",
      "reranker가 정답 문서를 후보 밖에서 만들 것으로 기대 → reranker 전 Recall@N을 별도 게이트로 둡니다.",
      "복합질문에서 한 문서만 찾고 답변 → subquery별 required evidence 슬롯을 모두 채운 뒤 bundle을 고정합니다."
    ],
    deliverables: ["QueryPlan schema", "entity/period resolver", "retriever adapters", "RRF implementation", "reranker adapter", "retrieval ablation report"],
    gates: ["질문마다 기업·기간·의도 해석 trace가 있음", "검색 전 as-of/version filter가 적용됨", "BM25 baseline이 고정 Gold에서 측정됨", "추가 후보는 단독 leg와 결합 성능이 모두 기록됨"],
    sources: [
      ["BM25 and Beyond", "https://www.ccs.neu.edu/home/vip/teach/IRcourse/IR_surveys/robertson_foundations.pdf"], ["Reciprocal Rank Fusion", "https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf"],
      ["Dense Passage Retrieval", "https://aclanthology.org/2020.emnlp-main.550/"], ["ColBERT", "https://arxiv.org/abs/2004.12832"],
      ["BGE-M3", "https://arxiv.org/abs/2402.03216"], ["Lost in the Middle", "https://aclanthology.org/2024.tacl-1.9/"]
    ]
  },
  {
    id: "evidence", number: "05", short: "계산·근거", color: "#e69a32",
    kicker: "Freeze facts before prose", title: "계산기와 Evidence Bundle",
    tagline: "모델에게 원문을 던지기 전에 유효 버전·셀·단위·피연산자를 확정합니다. 계산 결과까지 입력 evidence_id가 따라갑니다.",
    why: "결정론적 계산도 잘못된 셀을 받으면 정확하게 틀립니다. 따라서 계산기는 값뿐 아니라 기간·단위·연결범위 호환성을 검사하고, 검증된 입력만 immutable bundle로 동결해야 합니다.",
    contract: {
      input: "QueryPlan, ranked evidence, 유효 version, 정규화된 Fact",
      output: "선택 근거·계산식·피연산자·결과·경고가 포함된 frozen EvidenceBundle",
      never: "LLM이 숫자를 다시 계산하거나, 단위가 다른 fact를 묵시적으로 합치거나, bundle 외 evidence_id를 추가하기"
    },
    flow: [
      ["Resolve cell", "헤더·단위·기간 확인"], ["Compatibility", "scope·currency·basis"],
      ["Calculate", "Decimal·명시 수식"], ["Trace", "operand evidence 전파"],
      ["Freeze", "bundle hash·revision"], ["Coverage", "필수 근거 충족 판정"]
    ],
    exampleTitle: "증감률 결과도 근거 그래프를 가집니다",
    exampleLead: "최종 결과만 저장하면 값이 틀렸을 때 원인을 찾을 수 없습니다. 계산 record는 식과 피연산자, 각 피연산자의 원문 셀을 함께 보존합니다.",
    exampleCaption: "CalculationRecord 개념 예시",
    example: `calculation_id: calc:Q-FIN-001:revenue_yoy
formula: (current - previous) / abs(previous) * 100
operands:
  current:
    value: 1234000000
    unit: KRW
    period: current_duration
    scope: consolidated
    evidence_id: E_CUR_REVENUE
  previous:
    value: 1100000000
    unit: KRW
    period: previous_duration
    scope: consolidated
    evidence_id: E_PREV_REVENUE
result: 12.18
rounding: HALF_UP, 2 decimals
bundle_hash: sha256(...)

CompatibilityMatrix
  currency: 같음 또는 명시 환산식이 있을 때만 허용
  scale: 원단위 정규화 후 계산, 원문 scale 보존
  scope: consolidated끼리 / separate끼리만 허용
  period: duration끼리 같은 basis, instant끼리 같은 기준일
  basis: QTD≠YTD, FY≠YTD — 명시 변환식 없으면 거절
  version: as_of에서 effective인 fact만 허용`,
    candidates: [
      { type: "base", badge: "기본안", name: "Typed Python + Decimal", role: "재무 계산 함수", strength: "수식·반올림·결측·0 분모를 명시적으로 통제하고 단위 테스트하기 쉽습니다.", risk: "계산 유형별 함수를 직접 관리해야 합니다.", rule: "핵심 재무·사건 계산의 정본" },
      { type: "challenger", badge: "집계 후보", name: "DuckDB SQL", role: "다기업·다기간 집계", strength: "Parquet fact를 직접 질의하고 SQL로 집계 근거를 재현하기 쉽습니다.", risk: "단일 질문의 provenance를 별도로 전파해야 합니다.", rule: "대량 비교·EDA 경로에서 비교" },
      { type: "challenger", badge: "프레임 후보", name: "Polars", role: "열 기반 변환·계산", strength: "명시적 schema와 빠른 dataframe 연산을 제공합니다.", risk: "단순 계산에 도입하면 provenance adapter만 늘어날 수 있습니다.", rule: "대량 fact 계산이 병목일 때" },
      { type: "control", badge: "금지 대조", name: "LLM 계산", role: "수식 계획·설명", strength: "질문을 계산 유형으로 분류하고 사람 설명을 만들 수 있습니다.", risk: "산술·단위·피연산자 선택이 비결정적입니다.", rule: "계산 실행 금지, plan 후보만 허용" }
    ],
    criteria: [
      ["Numerical exactness", "정답 값과 허용 반올림 규칙을 코드로 비교합니다."],
      ["Operand accuracy", "각 피연산자가 올바른 기간·scope·단위·effective version인지 평가합니다."],
      ["Provenance completeness", "모든 결과가 입력 evidence_id와 수식으로 역추적되는 비율"],
      ["Compatibility rejection", "단위·기간·연결범위가 안 맞는 계산을 얼마나 정확히 거절하는지"],
      ["Determinism", "동일 bundle과 코드 revision에서 결과·hash가 동일한지"],
      ["Coverage", "지원 계산 유형과 unsupported/insufficient 분류가 명확한지"]
    ],
    measurementNote: "최종 숫자 exact match만 보면 우연히 맞은 계산을 통과시킵니다. operand와 evidence_id까지 함께 맞아야 계산 성공으로 봅니다.",
    recommendation: {
      base: "Typed Fact → 호환성 gate → Python Decimal 계산 함수 → provenance record → immutable EvidenceBundle",
      challenger: "대량 기업 비교와 반복 집계에 DuckDB SQL 또는 Polars를 사용하되 동일 CalculationRecord adapter로 출력",
      reason: "온라인 질문은 단순·검증 가능한 함수를, 대량 분석은 열 기반 엔진을 사용하면서 결과 계약은 하나로 유지합니다.",
      promote: "후보 엔진이 같은 operand set에서 exact result와 provenance를 유지하며 실제 병목을 줄일 때만 경로에 추가"
    },
    failures: [
      "누적 9개월과 단일 3개월을 직접 비교 → period_basis 호환성 검사를 통과하지 못하면 거절합니다.",
      "백만원 값을 원 단위 값과 더함 → normalize 후 원단위와 원문 scale을 모두 보존합니다.",
      "연결 매출과 별도 영업이익으로 비율 계산 → scope 불일치 gate가 차단합니다.",
      "정정 전 fact가 피연산자에 남음 → bundle freeze 전 effective version을 다시 검증합니다.",
      "0 또는 결측 분모를 0%로 출력 → undefined/insufficient 상태로 반환합니다."
    ],
    deliverables: ["Fact compatibility rules", "calculation library", "CalculationRecord schema", "EvidenceBundle schema", "numeric regression tests", "bundle coverage checker"],
    gates: ["지원 수식마다 정상·결측·0분모·단위불일치 테스트가 있음", "모든 결과가 operand evidence로 역추적됨", "bundle 외 근거를 생성기가 참조할 수 없음", "coverage 미달은 answered로 넘어가지 않음"],
    sources: [
      ["프로젝트 숫자·비교 규칙", "../disclosure_data_principles.md#10-결정론적-알고리즘"], ["프로젝트 표 계산 원칙", "../llm_system_principles.md#7-표와-숫자는-프로그램으로-계산한다"],
      ["DuckDB Parquet", "https://duckdb.org/docs/stable/data/parquet/overview"], ["Polars documentation", "https://docs.pola.rs/"]
    ]
  },
  {
    id: "llm", number: "06", short: "LLM", color: "#df6856",
    kicker: "Generate inside the fence", title: "HyperCLOVA X 연결",
    tagline: "애플리케이션이 검색과 계산을 통제하고, 모델은 동결된 EvidenceBundle을 사람이 읽는 claim으로 바꿉니다. 자체 구축의 핵심은 모델보다 이 경계입니다.",
    why: "언어모델의 다음 토큰 목적은 접수번호 존재성이나 숫자 진위를 보장하지 않습니다. 따라서 모델에게 임의 도구·원문 전체·자유 인용을 주지 않고, 허용된 evidence_id만 선택하는 구조화 출력을 요구합니다.",
    contract: {
      input: "사용자 질문, QueryPlan 요약, frozen EvidenceBundle, 허용 출력 schema",
      output: "status·atomic claims·선택 evidence_ids·calculation refs·warnings를 가진 ModelDraft",
      never: "bundle 밖 사실 보충, 접수번호 필드 제공, 숫자 재계산, 투자 의견·미래 예측"
    },
    flow: [
      ["Prompt boundary", "명령과 evidence 분리"], ["Schema", "status·claims·IDs"],
      ["HCX generate", "낮은 변동성 설정"], ["Parse", "JSON/schema validation"],
      ["Retry once", "형식 오류만 제한"], ["ModelDraft", "아직 미검증 상태"]
    ],
    exampleTitle: "모델 출력과 최종 응답은 서로 다른 객체입니다",
    exampleLead: "모델이 만든 답은 초안입니다. 서버는 검증을 통과한 claim만 남기고 evidence_id를 실제 접수번호와 locator로 변환합니다.",
    exampleCaption: "ModelDraft 구조",
    example: `{
  "status": "answered",
  "answer": "연결 매출액은 전기 대비 12.18% 증가했습니다.",
  "claims": [{
    "claim_id": "C1",
    "text": "연결 매출액은 전기 대비 12.18% 증가했습니다.",
    "evidence_ids": ["E_CUR_REVENUE", "E_PREV_REVENUE"],
    "calculation_id": "calc:Q-FIN-001:revenue_yoy"
  }],
  "warnings": []
}

서버만 하는 일:
bundle evidence_id → 요청별 짧은 alias(E1, E2) 발급
ModelDraft alias가 allowlist 안인지 membership 검사
alias → 접수번호·섹션·표·셀 변환
claim 검증 → VerifiedResponse 승격

강제 경계:
HCX 입력·출력 schema에 접수번호 필드를 두지 않음
bundle 밖 alias는 schema 후 서버 validator에서 즉시 실패`,
    candidates: [
      { type: "base", badge: "기본안", name: "HCX structured output", role: "근거 제한 최종 문장화", strength: "지원 모델에서 JSON schema 기반 출력을 사용해 ModelDraft 계약을 안정화할 수 있습니다.", risk: "모델별 기능 지원이 다르며 사실성은 별도 검증이 필요합니다.", rule: "구현 직전 공식 지원표 재확인" },
      { type: "challenger", badge: "도구 후보", name: "HCX function calling / RAG Reasoning", role: "도구 선택형 질의 처리", strength: "공식 API가 tool call과 문서 ID 인용 경로를 제공합니다.", risk: "tuned model·thinking·structured output과의 조합 제한이 있을 수 있습니다.", rule: "앱 주도 FSM보다 품질 이득이 있을 때" },
      { type: "challenger", badge: "튜닝 후보", name: "Instruction tuning / RAFT 방식", role: "형식·거절·distractor 무시 학습", strength: "반복 행동 오류를 데이터로 교정할 가능성이 있습니다.", risk: "사실을 외우게 하면 정정·출처 갱신이 어렵고 지원 모델 제약이 있습니다.", rule: "untuned 오류 taxonomy가 쌓인 뒤 ablation" },
      { type: "base", badge: "오케스트", name: "Custom finite-state workflow", role: "검색·계산·생성 순서 통제", strength: "허용 단계와 재시도 수가 코드에 명확해 감사와 디버깅이 쉽습니다.", risk: "복잡한 분기가 늘면 자체 상태관리 코드가 커집니다.", rule: "초기 기본 경로" },
      { type: "challenger", badge: "프레임 후보", name: "Haystack / LangGraph", role: "파이프라인·상태 그래프", strength: "구성요소 연결, 분기, 관측 hook을 재사용할 수 있습니다.", risk: "작은 그래프에는 추상화와 의존성만 늘 수 있습니다.", rule: "분기·재개·관측 요구가 실제로 커질 때" }
    ],
    criteria: [
      ["Schema validity", "ModelDraft JSON과 enum·ID 배열이 schema를 통과하는 비율"],
      ["Grounded claim precision", "각 atomic claim이 선택 evidence에서 실제로 지지되는 비율"],
      ["Citation selection", "모델이 허용 evidence_id 중 필요한 것만 정확히 고르는지"],
      ["Number preservation", "CalculationRecord 결과를 변형·재반올림하지 않고 복사하는지"],
      ["Refusal behavior", "bundle coverage가 부족할 때 answered 대신 insufficient를 선택하는지"],
      ["Cost / latency", "입력 evidence 토큰·출력 토큰·재시도율·p95를 기록"]
    ],
    measurementNote: "temperature 0 또는 구조화 출력은 변동성과 형식 오류를 줄일 뿐 사실성을 증명하지 않습니다. grounded claim은 다음 검증 단계에서 별도 판정합니다.",
    recommendation: {
      base: "애플리케이션 주도 finite-state workflow + frozen bundle + 구조화 출력을 지원하는 비튜닝 HCX 모델 + 외부 schema validator",
      challenger: "function calling/RAG Reasoning 경로와 tuned 행동 모델을 각각 분리 실험하며, 한 모델이 모든 기능을 동시에 지원한다고 가정하지 않음",
      reason: "검색·연산 권한을 코드에 남기고 HCX는 언어화에 집중하면 제품 기능 제약이 바뀌어도 핵심 근거 계약을 보존할 수 있습니다.",
      promote: "동일 oracle/retrieval bundle에서 grounded claim·거절·형식 안정성이 개선되고 비용 증가가 허용될 때만 후보 채택"
    },
    failures: [
      "모델이 존재하지 않는 접수번호 생성 → HCX schema에 접수번호 필드를 두지 않고 요청별 evidence alias allowlist만 검증합니다.",
      "공시 원문 안의 ‘이전 지시 무시’를 명령으로 실행 → evidence를 별도 데이터 영역으로 감쌉니다.",
      "튜닝 모델에서 function calling이 된다고 가정 → 모델별 공식 지원표를 구현 revision에 고정합니다.",
      "형식 오류마다 무한 재생성 → schema retry는 제한하고 반복 실패는 검증 단계로 넘기지 않습니다.",
      "HCX가 계산 결과를 다시 설명하며 숫자를 바꿈 → calculation_id와 표시값을 서버가 최종 삽입합니다."
    ],
    deliverables: ["Prompt contract", "ModelDraft JSON schema", "HCX client adapter", "finite-state orchestration", "model capability manifest", "prompt regression set"],
    gates: ["bundle 밖 evidence alias는 서버 membership validator를 통과할 수 없음", "HCX draft에 접수번호 필드가 없고 서버만 실제 인용을 삽입", "schema 실패와 grounded 실패가 구분됨", "계산값은 서버 소유 필드로 유지", "모델/API revision과 파라미터가 모든 run에 기록됨"],
    sources: [
      ["CLOVA Studio concepts", "https://guide.ncloud-docs.com/docs/en/clovastudio-info"], ["CLOVA Studio models", "https://guide.ncloud-docs.com/docs/en/clovastudio-model"],
      ["Function calling API", "https://api.ncloud-docs.com/docs/en/clovastudio-chatcompletionsv3-fc"], ["Embedding v2", "https://guide.ncloud-docs.com/docs/en/clovastudio-explorer03"],
      ["Haystack pipelines", "https://docs.haystack.deepset.ai/docs/pipelines"], ["LangGraph", "https://langchain-ai.github.io/langgraph/"], ["RAFT paper", "https://arxiv.org/abs/2403.10131"]
    ]
  },
  {
    id: "verification", number: "07", short: "검증·거절", color: "#7163b3",
    kicker: "Trust code, verify language", title: "Claim 검증과 답변 거절",
    tagline: "접수번호·버전·숫자는 코드로, 문장 의미는 선택적 semantic verifier로 검사합니다. 근거 부족은 실패가 아니라 정상 응답입니다.",
    why: "같은 생성 모델에게 ‘네 답이 맞니?’라고 묻는 것만으로는 검증이 되지 않습니다. 존재성·숫자·단위·시점처럼 판정 가능한 항목을 먼저 규칙으로 검사하고, 의미적 함의만 제한적으로 모델 판정에 맡깁니다.",
    contract: {
      input: "ModelDraft, EvidenceBundle, evidence registry, CalculationRecord, 답변 가능성 정책",
      output: "검증된 claim만 포함한 VerifiedResponse 또는 명시적 거절과 reason codes",
      never: "검증 실패 claim을 경고만 붙여 그대로 노출하거나, 낮은 검색 점수 하나로 답변 불가 판정"
    },
    flow: [
      ["Schema", "필드·enum·ID"], ["Existence", "evidence registry"],
      ["PIT/version", "효력 재검사"], ["Numeric", "값·단위·수식"],
      ["Semantic", "claim-evidence 함의"], ["Policy", "통과·삭제·거절"]
    ],
    exampleTitle: "검증 결과는 이유 코드까지 남깁니다",
    exampleLead: "거절 문장만 저장하면 개선 지점을 찾을 수 없습니다. 어떤 계층이 왜 막았는지 구조화하면 오류 비중이 높은 한 계층만 고칠 수 있습니다.",
    exampleCaption: "VerificationReport 예시",
    example: `{
  "status": "insufficient_evidence",
  "claim_results": [
    {"claim_id":"C1", "schema":true, "evidence_exists":true,
     "version_valid":true, "numeric_valid":false,
     "reason":"UNIT_SCOPE_UNRESOLVED"}
  ],
  "response_policy": "reject_whole_answer",
  "user_message": "공시에서 단위를 확정할 수 없어 답변할 수 없습니다.",
  "trace_id": "verify:..."
}`,
    candidates: [
      { type: "base", badge: "기본안", name: "Deterministic validators", role: "ID·버전·수치·단위 검사", strength: "정답이 명확한 항목을 재현 가능하게 판정하고 실패 원인을 설명합니다.", risk: "자연어 paraphrase의 함의까지 다루기는 어렵습니다.", rule: "항상 첫 번째 검증층" },
      { type: "challenger", badge: "의미 후보", name: "HCX judge prompt", role: "claim-evidence 함의 후보", strength: "한국어 공시 문장의 의미적 지지를 판정하는 구현이 빠릅니다.", risk: "생성기와 상관된 오류와 비결정성이 있습니다.", rule: "사람 라벨 검증셋에서 threshold 보정 후 보조" },
      { type: "challenger", badge: "의미 후보", name: "NLI / cross-encoder", role: "entailment 분류", strength: "생성과 분리된 모델로 claim-evidence pair를 일관되게 점수화할 수 있습니다.", risk: "한국 공시·표 직렬화 도메인 전이가 미확인입니다.", rule: "HCX judge보다 오류/비용이 개선될 때" },
      { type: "control", badge: "회귀 지표", name: "RAGAS", role: "faithfulness 등 자동 평가", strength: "개발 중 대량 비교와 추세 모니터링에 유용합니다.", risk: "자동 judge 결과를 실제 정답으로 오인할 수 있습니다.", rule: "오프라인 보조 지표, 최종 gate 단독 사용 금지" },
      { type: "challenger", badge: "정책 후보", name: "Claim 삭제 vs 전체 거절", role: "실패 시 응답 정책", strength: "독립 claim만 실패한 경우 나머지 답을 보존할 수 있습니다.", risk: "삭제 후 질문 요구사항을 충족하지 못할 수 있습니다.", rule: "requirement coverage 검사를 함께 통과할 때" }
    ],
    criteria: [
      ["Citation correctness", "인용 evidence가 해당 claim을 실제로 지지하는지"],
      ["Citation completeness", "검증 가능한 모든 claim에 필요한 근거가 빠짐없이 붙었는지"],
      ["Citation precision", "불필요하거나 틀린 근거가 함께 인용되지 않았는지"],
      ["False-answer rate", "답할 수 없는 질문에 answered를 반환한 비율을 핵심 위험지표로 둡니다."],
      ["Refusal precision/recall", "답할 수 있는 질문까지 과도하게 거절하지 않는지도 함께 측정합니다."],
      ["Verifier calibration", "semantic score 구간별 실제 정답률과 threshold 민감도를 봅니다."]
    ],
    measurementNote: "검증기는 정확도만 높이면 끝이 아닙니다. false answer를 낮추면서도 과도한 거절로 requirement coverage를 훼손하지 않는 operating point를 찾아야 합니다.",
    recommendation: {
      base: "schema → evidence 존재 → PIT/version → 수치·단위·계산 검증 → 선택적 semantic check → 정책 engine",
      challenger: "HCX judge와 별도 NLI를 동일 claim 검증셋에서 비교하고, 규칙으로 판정 가능한 항목은 두 모델에 맡기지 않음",
      reason: "명확한 사실은 코드가 책임지고 애매한 자연어만 모델이 맡으면 설명 가능성과 환각 억제를 동시에 확보합니다.",
      promote: "후보가 false-answer rate를 낮추면서 answerable recall과 비용 한도를 만족하고 calibration이 안정적일 때"
    },
    failures: [
      "답은 맞지만 다른 접수번호를 인용 → citation correctness 실패로 처리합니다.",
      "표 수치는 맞지만 단위가 틀림 → numeric validator가 value+unit+scale을 함께 비교합니다.",
      "모델 judge가 자기 답을 항상 지지 → 독립 사람 라벨과 deterministic check로 보정합니다.",
      "부분 claim 실패 후 남은 답이 질문을 충족하지 못함 → requirement coverage 재검사 후 전체 거절합니다.",
      "prompt injection이 검증 메시지를 통과 → 사용자/공시 문자열을 명령으로 실행하지 않고 도구 권한을 주지 않습니다."
    ],
    deliverables: ["validator registry", "VerificationReport schema", "reason code taxonomy", "abstention policy", "semantic verifier benchmark", "adversarial regression set"],
    gates: ["접수·버전·숫자 검증이 모델 없이 실행됨", "false-answer와 과거절을 동시에 측정", "모든 거절에 reason code가 있음", "검증 실패 응답이 외부 API schema에서도 명확히 표현됨"],
    sources: [
      ["ALCE citation evaluation", "https://arxiv.org/abs/2305.14627"], ["FActScore", "https://arxiv.org/abs/2305.14251"],
      ["RAGAS metrics", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/"], ["OWASP Prompt Injection", "https://genai.owasp.org/llmrisk/llm01-prompt-injection/"],
      ["Spotlighting", "https://arxiv.org/abs/2403.14720"]
    ]
  },
  {
    id: "server", number: "08", short: "서버·운영", color: "#b24b6b",
    kicker: "Reproduce first, scale second", title: "API·서버·관측 환경",
    tagline: "로컬에서 동일 revision을 재현하는 Compose 환경을 먼저 만들고, 품질 회귀를 통과한 동일 컨테이너를 NCP로 옮깁니다. 클라우드는 품질 알고리즘이 아니라 실행 환경입니다.",
    why: "서버를 일찍 만드는 이유는 확장 때문이 아니라 재현성과 통합 계약 때문입니다. 다만 Kubernetes나 분산 벡터 클러스터를 먼저 도입하면 파서·검색 오류보다 운영 오류를 디버깅하게 됩니다.",
    contract: {
      input: "검증된 pipeline package, DB/index revision, HCX credentials, 외부 응답 adapter",
      output: "동일 입력에 추적 가능한 응답을 주는 API, ingestion worker, 관측·배포·복구 절차",
      never: "API 프로세스가 raw를 직접 수정, 요청마다 인덱스 재생성, 로그에 원문·비밀키·비공개 reasoning 저장"
    },
    flow: [
      ["API ingress", "schema·rate limit"], ["Query service", "FSM orchestration"],
      ["Data services", "Postgres·search·object"], ["HCX egress", "timeout·retry budget"],
      ["Observability", "trace·metric·reason"], ["Deploy", "same image·revision pin"]
    ],
    exampleTitle: "요청 하나를 계층별 trace로 봅니다",
    exampleLead: "최종 지연만 보면 병목을 찾을 수 없습니다. 질의해석·검색·rerank·HCX·검증 시간을 분리하고 각 결과가 사용한 revision을 함께 기록합니다.",
    exampleCaption: "API와 trace 개념 예시",
    example: `POST /v1/answer
{ "question_id": "Q-001", "question": "...", "as_of": null }

Trace
  request.validate      4 ms
  query.resolve        38 ms
  retrieve.lexical     51 ms
  retrieve.dense       73 ms
  rerank              112 ms
  hcx.generate        840 ms
  verify               29 ms

Pinned revisions
  corpus=corpus:sha256...
  parser=parser:v1
  index=index:v3
  prompt=prompt:v5
  model=hcx:model-revision`,
    candidates: [
      { type: "base", badge: "API", name: "FastAPI", role: "질문·관리 API", strength: "Python 타입/schema 생태계와 비동기 I/O를 활용해 현재 파이프라인과 직접 연결하기 쉽습니다.", risk: "CPU 파싱 작업을 API worker에서 실행하면 요청이 막힙니다.", rule: "동기 질의 경로의 기본" },
      { type: "base", badge: "로컬", name: "Docker Compose", role: "API·DB·검색 재현", strength: "여러 컨테이너와 네트워크·볼륨을 한 파일로 정의해 팀 환경 차이를 줄입니다.", risk: "고가용성·자동확장 자체를 제공하지 않습니다.", rule: "개발·통합·데모 기본 환경" },
      { type: "challenger", badge: "작업큐", name: "RQ / Dramatiq", role: "ingestion·embedding 배치", strength: "가벼운 Python worker로 API와 긴 작업을 분리할 수 있습니다.", risk: "broker와 실패·재시도 정책을 운영해야 합니다.", rule: "실제 비동기 ingestion 작업이 생길 때" },
      { type: "challenger", badge: "작업큐", name: "Celery", role: "복잡한 배치·스케줄", strength: "성숙한 task routing, retry, scheduling 생태계를 갖습니다.", risk: "현재 규모에는 설정과 운영 복잡도가 과할 수 있습니다.", rule: "여러 queue·schedule·workflow가 필요할 때" },
      { type: "base", badge: "관측", name: "OpenTelemetry", role: "trace·metric 문맥", strength: "벤더 중립 instrumentation으로 계층별 지연과 오류를 연결할 수 있습니다.", risk: "수집·저장 backend는 별도로 정해야 합니다.", rule: "초기 trace ID부터 적용" },
      { type: "challenger", badge: "클라우드", name: "NCP VPC stack", role: "제출·운영 환경", strength: "Server, Cloud DB for PostgreSQL, Object Storage, Container Registry로 로컬 역할을 대응시킬 수 있습니다.", risk: "비용·네트워크·권한·서비스 가용 사양은 실제 계정에서 확인해야 합니다.", rule: "Compose E2E가 통과한 동일 image만 이동" }
    ],
    criteria: [
      ["E2E correctness", "로컬과 배포 환경이 같은 Gold 응답·reason code·revision을 내는지"],
      ["Latency", "단계별 p50/p95와 timeout·retry가 tail latency에 미치는 영향"],
      ["Throughput", "동시 질문에서 정확도·오류율을 유지하며 처리 가능한 요청량"],
      ["Recoverability", "DB backup restore, index rebuild, worker 재시작 후 중복 처리 여부"],
      ["Observability", "한 trace에서 검색 근거·HCX 호출·검증 결과와 revision을 연결 가능한지"],
      ["Security / cost", "secret 분리·최소 권한·외부 egress와 요청당 인프라/HCX 비용"]
    ],
    measurementNote: "서버 후보는 최대 처리량만으로 고르지 않습니다. 정확도 회귀 없이 재현·복구·관측 가능한 가장 단순한 구성이 우선입니다.",
    recommendation: {
      base: "FastAPI + ingestion 전용 worker + PostgreSQL/OpenSearch/Object layer + Docker Compose + OpenTelemetry trace",
      challenger: "같은 컨테이너를 NCP Server/Container Registry에 배치하고 Cloud DB for PostgreSQL·Object Storage로 상태 계층만 교체",
      reason: "알고리즘과 인프라를 분리해 로컬에서 검증한 artifact를 그대로 이동하고, 운영 서비스는 측정된 병목이 있을 때만 확장합니다.",
      promote: "E2E Gold 결과 동일, p95·동시성·복구·비용 기준 통과, secret/egress 검토 완료 후 클라우드 구성을 기본 배포로 승격"
    },
    failures: [
      "API worker가 PDF parsing까지 수행해 timeout → ingestion과 온라인 질의 경로를 분리합니다.",
      "재시도로 HCX가 중복 호출돼 비용 증가 → idempotency key와 단계별 retry budget을 둡니다.",
      "DB와 search index revision 불일치 → readiness check에서 revision mismatch를 차단합니다.",
      "로그에 공시 원문 전체·API key 저장 → ID·hash·reason code만 남기고 secret은 별도 관리합니다.",
      "처음부터 Kubernetes 도입 → Compose 한계가 동시성/가용성 측정으로 드러난 뒤 검토합니다."
    ],
    deliverables: ["OpenAPI contract", "Docker Compose environment", "ingestion worker", "revision/readiness endpoint", "OpenTelemetry trace", "backup/rebuild runbook", "E2E load report"],
    gates: ["새 환경에서 한 명령으로 동일 stack 실행", "모든 응답에 trace_id와 revision pin이 있음", "DB 복구와 index rebuild가 실제 검증됨", "클라우드와 로컬 Gold 결과가 동일함"],
    sources: [
      ["FastAPI deployment", "https://fastapi.tiangolo.com/deployment/"], ["Docker Compose", "https://docs.docker.com/compose/"],
      ["OpenTelemetry Python", "https://opentelemetry.io/docs/languages/python/instrumentation/"], ["NCP Cloud DB for PostgreSQL", "https://guide.ncloud-docs.com/docs/en/clouddbforpostgresql-overview"],
      ["NCP Container Registry", "https://guide.ncloud-docs.com/release-20260423/docs/en/containerregistry-overview"], ["NCP VPC Server", "https://guide.ncloud-docs.com/docs/en/server-create-vpc"]
    ]
  }
];
