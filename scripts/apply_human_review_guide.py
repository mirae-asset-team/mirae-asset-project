from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GUIDE = ROOT / "references/research/gold_human_review_guide.md"
DEFAULT_GOLD = ROOT / "data/derived/gold_qa.jsonl"
DEFAULT_BACKUP = ROOT / "data/derived/gold_qa.pre_human_review_2026-08-15.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_guide(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)(?=^### \d+\. q)", text)[1:]
    decisions: dict[str, dict[str, str]] = {}
    for section in sections:
        match = re.search(r"(?m)^### \d+\. (q\S+)$", section)
        if match is None:
            continue
        question_id = match.group(1)
        checked = []
        for label, token in (
            ("approved", "- [x] 승인"), ("modify", "- [x] 수정 필요"), ("hold", "- [x] 보류")
        ):
            if token in section.lower():
                checked.append(label)
        if len(checked) != 1:
            raise ValueError(f"{question_id}: exactly one decision is required, got {checked}")
        note_match = re.search(r"(?m)^- 메모:[ \t]*(.*)$", section)
        value_match = re.search(r"(?m)^- 수정값:[ \t]*(.*)$", section)
        decisions[question_id] = {
            "decision": checked[0],
            "note": (note_match.group(1).strip() if note_match else ""),
            "correction": (value_match.group(1).strip() if value_match else ""),
        }
    if len(decisions) != 23:
        raise ValueError(f"expected 23 guide decisions, got {len(decisions)}")
    return decisions


def apply_review(
    *, guide: Path, gold: Path, backup: Path, reviewer: str, reviewed_at: str
) -> dict[str, int]:
    decisions = parse_guide(guide)
    records = load_jsonl(gold)
    record_ids = {record["question_id"] for record in records}
    if set(decisions) != record_ids:
        raise ValueError("guide question IDs and Gold question IDs differ")
    if not backup.exists():
        shutil.copy2(gold, backup)

    counts = {"approved": 0, "modify": 0, "hold": 0}
    for record in records:
        decision = decisions[record["question_id"]]
        counts[decision["decision"]] += 1
        review = record["review"]
        human_note = f"human_review_guide={decision['decision']}"
        if decision["note"]:
            human_note += f"; note={decision['note']}"
        if decision["correction"]:
            human_note += f"; correction={decision['correction']}"
        existing = str(review.get("notes") or "")
        if "human_review_guide=" in existing:
            existing = existing.split(" [human_review_guide=", 1)[0]
        review["notes"] = f"{existing} [{human_note}]".strip()

        if decision["decision"] == "approved":
            record["answer_origin"] = "human_verified"
            review["status"] = "approved"
            review["reviewer"] = reviewer
            review["reviewed_at"] = reviewed_at
            for evidence in record.get("evidence", []):
                if evidence.get("locator", {}).get("kind") == "table_cell":
                    evidence["table_structure_status"] = "human_validated"
        else:
            record["answer_origin"] = "model_generated"
            review["status"] = "candidate"
            review["reviewer"] = reviewer
            review["reviewed_at"] = reviewed_at

    temporary = gold.with_suffix(gold.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    temporary.replace(gold)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply checked human review guide decisions to Gold candidates")
    parser.add_argument("--guide", type=Path, default=DEFAULT_GUIDE)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--reviewer", default="ddolmange00")
    args = parser.parse_args()
    reviewed_at = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    counts = apply_review(
        guide=args.guide, gold=args.gold, backup=args.backup,
        reviewer=args.reviewer, reviewed_at=reviewed_at,
    )
    print(json.dumps({**counts, "reviewer": args.reviewer, "reviewed_at": reviewed_at}, ensure_ascii=False))


if __name__ == "__main__":
    main()
