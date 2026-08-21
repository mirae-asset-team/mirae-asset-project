from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/derived/disclosure_corpus.sqlite"
GOLD = ROOT / "data/derived/gold_qa.jsonl"
MODEL_DIR = ROOT / "data/derived/model_review"
OUTPUT = ROOT / "references/research/gold_human_review_guide.md"

PRIORITY = [
    "q005_bio_amount_change_as_of_july",
    "q015_hanwha_aero_pdf_table_unanswerable",
    "q001_bio_original_contract_amount",
    "q002_bio_original_counterparty",
    "q004_bio_july_current_contract_amount",
    "q006_celltrion_ct_p39_title",
    "q009_rainbow_new_common_shares",
    "q010_skhynix_treasury_disposal_shares",
    "q011_rainbow_holding_reporter",
    "q019_korea_zinc_annual_not_current",
    "q020_bio_aug_different_contract_not_same_event",
    "q021_hanwha_ocean_holding_original_not_current",
    "q022_hanwha_ocean_rights_issue_correction_current",
    "q023_korea_zinc_buyback_correction_not_current",
    "q003_bio_original_operating_profit_unanswerable",
    "q007_celltrion_approval_done_unanswerable",
    "q008_celltrion_prompt_injection",
    "q012_hmm_unresolved_latest_unanswerable",
    "q013_woori_stock_price_unanswerable",
    "q014_lginnotek_next_year_sales_unanswerable",
    "q016_hanwha_ocean_pdf_table_unanswerable",
    "q017_kb_pdf_audit_table_unanswerable",
    "q018_shinhan_target_price_unanswerable",
]

SEARCH_HINTS = {
    "q001_bio_original_contract_amount": "계약금액(원) 또는 240,993,039,040",
    "q002_bio_original_counterparty": "계약상대 또는 Pfizer Ireland Pharmaceuticals",
    "q003_bio_original_operating_profit_unanswerable": "영업이익 또는 기여",
    "q004_bio_july_current_contract_amount": "계약금액(원) 또는 495,278,073,440",
    "q005_bio_amount_change_as_of_july": "계약금액(원), 240,993,039,040, 495,278,073,440",
    "q006_celltrion_ct_p39_title": "제목 또는 CT-P39",
    "q007_celltrion_approval_done_unanswerable": "향후 계획, 허가 신청, 품목허가",
    "q008_celltrion_prompt_injection": "허가 신청 또는 주가 상승",
    "q009_rainbow_new_common_shares": "보통주식 또는 1,940,200",
    "q010_skhynix_treasury_disposal_shares": "처분예정주식 또는 495,472",
    "q011_rainbow_holding_reporter": "보고자 또는 삼성전자",
    "q012_hmm_unresolved_latest_unanswerable": "정정, 보유주식수, 최초제출일",
    "q013_woori_stock_price_unanswerable": "적정 주가",
    "q014_lginnotek_next_year_sales_unanswerable": "2026, 매출액, 전망",
    "q015_hanwha_aero_pdf_table_unanswerable": "연결, 매출액",
    "q016_hanwha_ocean_pdf_table_unanswerable": "연결, 매출액",
    "q017_kb_pdf_audit_table_unanswerable": "감사의견",
    "q018_shinhan_target_price_unanswerable": "목표주가",
    "q019_korea_zinc_annual_not_current": "정정, 사업보고서, 20260325000008",
    "q020_bio_aug_different_contract_not_same_event": "계약상대, 계약명, 최초계약일",
    "q021_hanwha_ocean_holding_original_not_current": "정정, 최초제출일, 20250905000003",
    "q022_hanwha_ocean_rights_issue_correction_current": "정정, 유상증자결정, 최초제출일",
    "q023_korea_zinc_buyback_correction_not_current": "정정, 자기주식취득결정, 최초제출일",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def answer_text(answer: dict[str, Any]) -> str:
    kind = answer.get("kind")
    if kind == "numeric":
        return f"{answer.get('value')} {answer.get('unit') or ''}".strip()
    if kind == "multi_numeric":
        return "; ".join(
            f"{item.get('label')}: {item.get('value')} {item.get('unit') or ''}".strip()
            for item in answer.get("values", [])
        )
    if kind == "text":
        return str(answer.get("text") or "")
    if kind == "unanswerable":
        return f"답변 불가 — {answer.get('reason') or ''}"
    values = answer.get("values", [])
    return ", ".join(str(value) for value in values) if values else str(kind)


def review_answer_text(review: dict[str, Any]) -> str:
    answer = review.get("answer", {})
    kind = answer.get("kind")
    values = answer.get("values", [])
    if kind in {"unanswerable", "uncertain"}:
        return "답변 불가" if kind == "unanswerable" else "불확실"
    return " / ".join(str(value) for value in values) or str(kind)


def load_model_reviews() -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for name in ("qwen", "grok", "codex"):
        for wrapped in load_jsonl(MODEL_DIR / f"{name}_reviews.jsonl"):
            review = wrapped["review"]
            output.setdefault(review["question_id"], {})[name] = review
    return output


def compact(value: str | None, limit: int = 500) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def source_rows(connection: sqlite3.Connection, filing_ids: list[str]) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in filing_ids)
    rows = connection.execute(
        f"""SELECT filing_id,source_id,source_path,detected_format,parse_status,role
            FROM source_document WHERE filing_id IN ({placeholders})
            ORDER BY filing_id,source_path""",
        filing_ids,
    ).fetchall()
    output: list[sqlite3.Row] = []
    for filing_id in filing_ids:
        group = [row for row in rows if row["filing_id"] == filing_id]
        preferred = [row for row in group if row["role"] in {"main", "pdf_main"}]
        output.extend(preferred or group[:1])
    return output


def evidence_rows(connection: sqlite3.Connection, evidence_ids: list[str]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        row = connection.execute(
            """SELECT c.evidence_id,c.filing_id,c.text_normalized,c.row_header_path_json,
                      c.column_header_path_json,c.locator_json,t.caption,t.unit_text
               FROM table_cell c JOIN table_record t ON t.table_id=c.table_id
               WHERE c.evidence_id=?""",
            (evidence_id,),
        ).fetchone()
        if row:
            output.append(
                {
                    "evidence_id": row[0], "filing_id": row[1], "text": row[2],
                    "row_headers": json.loads(row[3]), "column_headers": json.loads(row[4]),
                    "locator": json.loads(row[5]), "caption": row[6], "unit": row[7],
                }
            )
            continue
        row = connection.execute(
            "SELECT evidence_id,filing_id,text_normalized,section_path_json,locator_json FROM fragment WHERE evidence_id=?",
            (evidence_id,),
        ).fetchone()
        if row:
            output.append(
                {
                    "evidence_id": row[0], "filing_id": row[1], "text": row[2],
                    "section_path": json.loads(row[3]), "locator": json.loads(row[4]),
                }
            )
    return output


def review_instruction(record: dict[str, Any]) -> str:
    qid = record["question_id"]
    if qid == "q005_bio_amount_change_as_of_july":
        return "원본과 2023-07-04 정정본에서 계약금액 두 개를 각각 찾아 순서와 단위가 맞는지만 확인합니다. Qwen 답은 무시하고 직접 숫자를 봅니다."
    if qid == "q001_bio_original_contract_amount":
        return "원문 표의 ‘계약금액(원)’이 240993039040인지 한 번만 대조합니다. 맞으면 승인합니다."
    if qid == "q015_hanwha_aero_pdf_table_unanswerable":
        return "PDF를 열고 Ctrl+F로 ‘매출액’을 한 번 검색합니다. 2026년 1분기 연결 매출액의 값·단위·기간을 모두 확인하면 수정 필요에 값/페이지/단위를 적고, 하나라도 확정할 수 없으면 ‘답변 불가’를 승인합니다."
    if record["question_type"] == "correction_aware":
        return "접수번호들이 같은 사건인지, 질문에 나온 접수번호의 현재 유효 여부가 후보 답과 같은지만 확인합니다."
    if record["answerability"] == "unanswerable":
        return "질문이 공시 근거 밖의 예측·평가·미검증 표를 요구하는지 확인합니다. 근거가 없으면 답을 만들지 않고 ‘답변 불가’를 승인합니다."
    if record["question_type"] == "table_cell":
        return "원문 표에서 같은 행 이름, 열 이름, 단위를 확인한 뒤 후보 숫자와 일치하는지 봅니다."
    return "원문에서 질문이 묻는 문구를 찾아 후보 답과 뜻이 같은지 확인합니다."


def build() -> str:
    records = {record["question_id"]: record for record in load_jsonl(GOLD)}
    reviews = load_model_reviews()
    lines = [
        "# Gold QA 사람 검수 안내서",
        "",
        "작성일: 2026-08-15  ",
        "대상: 미래에셋 AI 공모전 팀  ",
        "목적: JSON과 DB를 직접 해석하지 않고 23개 QA 후보를 원문과 대조함",
        "",
        "## 오늘은 이것만 보면 됨",
        "",
        "피곤하면 **q005 한 건만 확인하고 멈춰도 됨**. Grok과 Codex는 두 계약금액을 맞혔지만 Qwen 14B는 계약상대를 답해 의견이 갈렸음. 이 숫자 두 개만 원문에서 확인하는 것이 현재 가장 가치가 큼.",
        "",
        "1. q005의 최초 금액 `240993039040원` 확인",
        "2. q005의 정정 후 금액 `495278073440원` 확인",
        "3. 두 숫자가 맞으면 q005의 `승인`에 체크",
        "",
        "그 다음 여유가 있을 때 q015와 q001을 봄. 나머지는 아래 순서대로 하루에 몇 개씩 처리해도 됨.",
        "검수 순서는 q005 → q015 → q001임. q001은 q005의 최초 금액과 겹치므로 마지막에 빠르게 재확인함.",
        "",
        "## 진행표",
        "",
        "- [ ] 1차: q005 불일치 해결",
        "- [ ] 2차: q015 거절 판단",
        "- [ ] 3차: q001 삼자 합의 표본 확인",
        "- [ ] 4차: 나머지 직접 사실 6건",
        "- [ ] 5차: 정정 계보 5건",
        "- [ ] 6차: 나머지 답변 불가 8건",
        "",
        "## 하지 않아도 되는 일",
        "",
        "- evidence ID, SHA, fragment/table/cell 개수를 다시 계산하지 않음.",
        "- JSON을 직접 수정하지 않음.",
        "- 정답 문장을 새로 예쁘게 쓰지 않음.",
        "- PDF에 표가 없으면 셀 좌표를 추측하지 않음.",
        "- 판단이 애매하면 승인하지 말고 `보류`에 체크함.",
        "",
        "대표님은 각 항목에서 원문과 후보 답을 비교한 뒤 `승인 / 수정 / 보류` 중 하나만 표시하면 됨. 실제 JSON 반영과 재검증은 Codex가 수행함.",
        "",
        "## 승인·수정·보류 기준",
        "",
        "- **승인:** 원문에서 후보 답 또는 답변 불가 사유를 확인했음.",
        "- **수정 필요:** 원문에서 정확한 정답·단위·현재본을 확인했지만 후보와 다름. 아는 수정값을 적음.",
        "- **보류:** 원문이 안 열림, 표/정정 관계가 모호함, 무엇이 맞는지 결론을 못 냄. 추측하지 않음.",
        "",
        "수정 유형은 `답 값 / 답변 가능 여부 / 질문 문구 / 근거·정정 연결` 중 하나를 표시함.",
        "",
        "## 파일 열기",
        "",
        "문서 안 원문 링크를 누름. 링크가 안 열리면 `data/public/official_dataset/raw/corpus/raw` 아래에서 접수번호 14자리를 검색함. 이 안내서는 같은 위치에서 직접 편집하고 저장한 뒤 Codex에 ‘검수 끝’이라고 전달하면 됨.",
        "",
        "## 표시 방법",
        "",
        "```text",
        "[x] 승인   [ ] 수정 필요   [ ] 보류",
        "수정값: (수정이 필요할 때만)",
        "메모: (선택)",
        "```",
        "",
        "## 23개 검수 항목",
        "",
    ]
    uri = f"file:{DB.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for index, question_id in enumerate(PRIORITY, start=1):
            record = records[question_id]
            model_reviews = reviews.get(question_id, {})
            lines.extend(
                [
                    f"### {index}. {question_id}",
                    "",
                    f"- **질문:** {record['question']}",
                    f"- **후보 답:** {answer_text(record['answer'])}",
                    f"- **판정 종류:** {record['question_type']} / {record['answerability']}",
                    f"- **원문 검색어:** `{SEARCH_HINTS[question_id]}`",
                    f"- **확인할 것:** {review_instruction(record)}",
                    "",
                    "#### 모델 의견",
                    "",
                ]
            )
            available = []
            for name, label in (("grok", "Grok 4.6"), ("qwen", "Qwen 2.5 14B"), ("codex", "Codex blind")):
                review = model_reviews.get(name)
                if review is not None:
                    available.append(name)
                    lines.append(
                        f"- {label}: {review['answerability']} / {review_answer_text(review)} / "
                        f"{review['support_verdict']} / 신뢰도 {review['confidence']}"
                    )
            missing_labels = [label for name, label in (("grok", "Grok"), ("qwen", "Qwen")) if name not in available]
            if missing_labels:
                lines.append(f"- 추가 모델 미실행: {', '.join(missing_labels)}")
            filings = list(record["candidate_filing_ids"])
            if record["question_type"] == "correction_aware":
                filings = list(
                    dict.fromkeys(filings + [item["filing_id"] for item in record.get("version_evidence", [])])
                )
            lines.extend(["", "#### 열어볼 원문", ""])
            for source in source_rows(connection, filings):
                relative = Path("../../data/public/official_dataset/raw/corpus") / Path(source["source_path"])
                lines.append(
                    f"- 접수 {source['filing_id']} · {source['detected_format']} · "
                    f"[{Path(source['source_path']).name}](<{relative.as_posix()}>)"
                )
            direct = evidence_rows(connection, [item["evidence_id"] for item in record.get("evidence", [])])
            if direct:
                lines.extend(["", "#### 자동으로 찾은 직접 근거", ""])
                for item in direct:
                    headers = item.get("row_headers", []) + item.get("column_headers", [])
                    header_text = " > ".join(str(value) for value in headers if value)
                    lines.append(f"- 접수 {item['filing_id']} · `{compact(item['text'])}`")
                    if header_text:
                        lines.append(f"  - 헤더 후보: `{compact(header_text, 250)}`")
                    if item.get("unit"):
                        lines.append(f"  - 표 단위 후보: `{item['unit']}`")
            elif record.get("version_evidence"):
                lines.extend(["", "#### 자동으로 찾은 정정 계보", ""])
                for item in record["version_evidence"]:
                    lines.append(
                        f"- {item['filing_id']}: {item['lineage_status']}, current={item['is_current']}, "
                        f"유효기간 {item['effective_from']} ~ {item['effective_to'] or '현재'}"
                    )
            lines.extend(
                [
                    "",
                    "#### 대표님 판정",
                    "",
                    "- [ ] 승인",
                    "- [ ] 수정 필요",
                    "- [ ] 보류",
                    "- 수정 유형: [ ] 답 값  [ ] 답변 가능 여부  [ ] 질문 문구  [ ] 근거·정정 연결",
                    "- 수정값:",
                    "- 메모:",
                    "",
                    "---",
                    "",
                ]
            )
    lines.extend(
        [
            "## 검수 완료 후",
            "",
            "체크한 문서를 Codex에 전달하면 됨. Codex가 승인 항목만 `human_verified/approved`로 반영하고, 수정값을 후보 답과 evidence에 적용한 뒤 Gold validator를 다시 실행함.",
            "",
            "모든 항목을 한 번에 끝낼 필요는 없음. q005 한 건만 끝내도 첫 번째 실질 검수 결과가 생김.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    content = build()
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({"output": str(OUTPUT), "bytes": OUTPUT.stat().st_size, "questions": 23}, ensure_ascii=False))


if __name__ == "__main__":
    main()
