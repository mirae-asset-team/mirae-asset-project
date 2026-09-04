from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from disclosure_db.model_review import REVIEW_SCHEMA, build_packets, compare_reviews, review_prompt


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/derived/disclosure_corpus.sqlite"
DEFAULT_GOLD = ROOT / "data/derived/gold_qa.jsonl"
DEFAULT_OUTPUT = ROOT / "data/derived/model_review"
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def sanitize_model_value(value: Any) -> Any:
    if isinstance(value, str):
        return ANSI_ESCAPE.sub("", value).replace("\r", "").strip()
    if isinstance(value, list):
        return [sanitize_model_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sanitize_model_value(item) for key, item in value.items()}
    return value


def parse_json_output(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    value: Any = json.loads(cleaned, strict=False)
    required = set(REVIEW_SCHEMA["required"])

    def find_contract(item: Any) -> dict[str, Any] | None:
        if isinstance(item, dict):
            if required <= set(item):
                return item
            for nested in item.values():
                found = find_contract(nested)
                if found is not None:
                    return found
        elif isinstance(item, list):
            for nested in item:
                found = find_contract(nested)
                if found is not None:
                    return found
        elif isinstance(item, str) and item.lstrip().startswith(("{", "[")):
            try:
                return find_contract(json.loads(item, strict=False))
            except json.JSONDecodeError:
                return None
        return None

    found = find_contract(value)
    if found is not None:
        return sanitize_model_value(found)
    raise ValueError("model output does not contain the review contract")


def run_qwen(prompt: str, model: str) -> dict[str, Any]:
    def invoke(input_text: str) -> str:
        payload = json.dumps(
            {
                "model": model, "prompt": input_text, "stream": False, "format": REVIEW_SCHEMA,
                "options": {"temperature": 0}, "keep_alive": "10m",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate", data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        if not isinstance(envelope.get("response"), str):
            raise ValueError("Ollama response field is missing")
        return envelope["response"].strip()

    raw = invoke(prompt)
    try:
        return parse_json_output(raw)
    except (json.JSONDecodeError, ValueError) as first_error:
        repair_prompt = (
            "아래 출력은 내용은 유지해야 하지만 JSON 문법이 잘못됐다. 설명·코드펜스 없이 주어진 "
            "JSON Schema에 맞는 JSON 객체 하나로만 수리하라. 새로운 사실을 추가하지 마라.\n\n"
            f"PARSER_ERROR: {first_error}\nBROKEN_OUTPUT:\n{raw}"
        )
        repaired = invoke(repair_prompt)
        try:
            return parse_json_output(repaired)
        except (json.JSONDecodeError, ValueError) as second_error:
            raise ValueError(
                f"qwen JSON repair failed: {second_error}; raw={raw[:1200]!r}; repaired={repaired[:1200]!r}"
            ) from second_error


def run_grok(prompt: str, model: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="blind-gold-review-") as temporary:
        prompt_path = Path(temporary) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        completed = subprocess.run(
            [
                "grok", "--prompt-file", str(prompt_path), "--cwd", temporary,
                "--model", model, "--json-schema", json.dumps(REVIEW_SCHEMA),
                "--output-format", "json", "--no-memory", "--no-subagents", "--disable-web-search",
                "--permission-mode", "dontAsk",
            ],
            text=True, encoding="utf-8", capture_output=True, check=True,
        )
    return parse_json_output(completed.stdout.strip())


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def load_review_file(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    output: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        wrapped = json.loads(line)
        wrapped = sanitize_model_value(wrapped)
        review = wrapped.get("review", {})
        if review.get("question_id"):
            output[str(review["question_id"])] = wrapped
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Blind multi-model review for Gold QA candidates")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--models", default="qwen,grok", help="comma-separated: qwen,grok")
    parser.add_argument("--qwen-model", default="qwen2.5:14b")
    parser.add_argument("--grok-model", default="grok-4.6")
    parser.add_argument("--question-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--packets-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="ignore saved model reviews and rerun")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    all_packets = build_packets(args.database, args.gold)
    records = [json.loads(line) for line in args.gold.read_text(encoding="utf-8").splitlines() if line.strip()]
    record_by_id = {record["question_id"]: record for record in records}
    write_jsonl(args.output / "blind_packets.jsonl", all_packets)
    packets = all_packets
    if args.question_id:
        selected = set(args.question_id)
        packets = [packet for packet in packets if packet["question_id"] in selected]
    if args.limit is not None:
        packets = packets[: args.limit]
    if args.packets_only:
        print(json.dumps({"packets": len(all_packets), "selected": len(packets), "output": str(args.output)}, ensure_ascii=False))
        return

    model_names = [name.strip() for name in args.models.split(",") if name.strip()]
    reviews_by_question: dict[str, list[dict[str, Any]]] = {packet["question_id"]: [] for packet in packets}
    errors: list[dict[str, str]] = []
    error_path = args.output / "errors.jsonl"
    persisted_errors: dict[tuple[str, str], dict[str, str]] = {}
    if error_path.exists():
        for line in error_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                persisted_errors[(str(item["question_id"]), str(item["model"]))] = item
    for model_name in model_names:
        output_path = args.output / f"{model_name}_reviews.jsonl"
        saved = {} if args.force else load_review_file(output_path)
        for packet in packets:
            question_id = packet["question_id"]
            if question_id in saved and saved[question_id].get("packet_sha256") == packet["packet_sha256"]:
                persisted_errors.pop((question_id, model_name), None)
                reviews_by_question[question_id].append(saved[question_id]["review"])
                continue
            prompt = review_prompt(packet)
            try:
                if model_name == "qwen":
                    review = run_qwen(prompt, args.qwen_model)
                    reviewer = args.qwen_model
                elif model_name == "grok":
                    review = run_grok(prompt, args.grok_model)
                    reviewer = args.grok_model
                else:
                    raise ValueError(f"unsupported model: {model_name}")
                reported_question_id = review.get("question_id")
                if reported_question_id != question_id:
                    review["question_id"] = question_id
                saved[question_id] = {
                    "reviewer_model": reviewer, "packet_sha256": packet["packet_sha256"], "review": review,
                }
                if reported_question_id != question_id:
                    saved[question_id]["model_reported_question_id"] = reported_question_id
                persisted_errors.pop((question_id, model_name), None)
                reviews_by_question[question_id].append(review)
                write_jsonl(output_path, list(saved.values()))
            except Exception as error:  # persist the failure and continue with other independent reviews
                item = {"question_id": question_id, "model": model_name, "error": str(error)}
                errors.append(item)
                persisted_errors[(question_id, model_name)] = item
                write_jsonl(error_path, list(persisted_errors.values()))
                if args.fail_fast:
                    raise
        write_jsonl(output_path, list(saved.values()))
    if persisted_errors:
        write_jsonl(error_path, list(persisted_errors.values()))
    elif error_path.exists():
        error_path.unlink()

    comparisons = [
        compare_reviews(record_by_id[packet["question_id"]], reviews_by_question[packet["question_id"]])
        for packet in packets
    ]
    write_jsonl(args.output / "comparison.jsonl", comparisons)
    summary = {
        "records": len(comparisons),
        "reviewer_models": model_names,
        "answerability_agreement": sum(bool(item["answerability_agreement"]) for item in comparisons),
        "exact_numeric_agreement": sum(bool(item["exact_numeric_agreement"]) for item in comparisons),
        "all_reviewers_supported": sum(bool(item["all_reviewers_supported"]) for item in comparisons),
        "silver_auto_pass": sum(bool(item["silver_auto_pass"]) for item in comparisons),
        "gold_promoted": 0,
        "errors": len(errors),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
