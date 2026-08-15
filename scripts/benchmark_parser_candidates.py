from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree, html

from disclosure_db.pipeline import load_manifest, sniff_format, source_files


def choose_samples(corpus: Path) -> list[tuple[dict[str, object], Path]]:
    rows = load_manifest(corpus / "manifest.jsonl")
    chosen: list[tuple[dict[str, object], Path]] = []
    for group in ("periodic", "major", "exchange", "holding"):
        candidates = []
        for row in rows:
            if row["doc_group"] != group:
                continue
            files = [path for path in source_files(corpus, row) if path.suffix.lower() == ".xml"]
            main = next((path for path in files if path.stem == row["rcept_no"]), None)
            if main:
                candidates.append((main.stat().st_size, row, main))
        candidates.sort(key=lambda item: item[0])
        for index in (0, len(candidates) // 2, len(candidates) - 1):
            _, row, path = candidates[index]
            chosen.append((row, path))
    return chosen


def lxml_direct(raw: bytes, detected_format: str) -> tuple[int, int, int]:
    if detected_format == "exchange_html":
        root = html.fromstring(raw.decode("utf-8"), parser=html.HTMLParser(encoding="utf-8", recover=True, no_network=True))
    else:
        root = etree.fromstring(
            raw,
            parser=etree.XMLParser(
                recover=True,
                no_network=True,
                resolve_entities=False,
                huge_tree=True,
                collect_ids=False,
            ),
        )
    tags = [element.tag.rsplit("}", 1)[-1].upper() for element in root.iter() if isinstance(element.tag, str)]
    counts = Counter(tags)
    return counts["TABLE"], counts["TR"], len(" ".join(root.itertext()))


def beautifulsoup_lxml(raw: bytes, detected_format: str) -> tuple[int, int, int]:
    text = raw.decode("utf-8")
    features = "lxml" if detected_format == "exchange_html" else "lxml-xml"
    soup = BeautifulSoup(text, features=features)
    tables = soup.find_all(lambda tag: tag.name and tag.name.casefold() == "table")
    rows = soup.find_all(lambda tag: tag.name and tag.name.casefold() == "tr")
    return len(tables), len(rows), len(soup.get_text(" "))


def run(corpus: Path, output: Path) -> dict[str, object]:
    samples = choose_samples(corpus)
    records = []
    for row, path in samples:
        raw = path.read_bytes()
        detected_format, _, _ = sniff_format(path)
        outputs = {}
        for name, function in (("lxml_direct", lxml_direct), ("beautifulsoup_lxml", beautifulsoup_lxml)):
            started = time.perf_counter()
            result = function(raw, detected_format)
            elapsed = time.perf_counter() - started
            outputs[name] = {"seconds": elapsed, "table_count": result[0], "row_count": result[1], "text_chars": result[2]}
        records.append(
            {
                "filing_id": row["rcept_no"],
                "doc_group": row["doc_group"],
                "bytes": len(raw),
                "detected_format": detected_format,
                "candidates": outputs,
            }
        )
    direct = [item["candidates"]["lxml_direct"]["seconds"] for item in records]
    soup = [item["candidates"]["beautifulsoup_lxml"]["seconds"] for item in records]
    mismatches = sum(
        item["candidates"]["lxml_direct"]["table_count"] != item["candidates"]["beautifulsoup_lxml"]["table_count"]
        or item["candidates"]["lxml_direct"]["row_count"] != item["candidates"]["beautifulsoup_lxml"]["row_count"]
        for item in records
    )
    summary = {
        "sample_count": len(records),
        "sampling": "min_median_max_main_source_per_doc_group",
        "lxml_total_seconds": sum(direct),
        "beautifulsoup_total_seconds": sum(soup),
        "beautifulsoup_over_lxml_ratio": sum(soup) / sum(direct),
        "structure_count_mismatches": mismatches,
        "decision": "lxml_direct",
        "reason": "동일 libxml2 계열 복구 결과를 더 직접적으로 제어하며, 좌표/오류 로그를 보존하고 중간 객체 비용을 줄인다.",
    }
    payload = {"summary": summary, "samples": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.corpus.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
