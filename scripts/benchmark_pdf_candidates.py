from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pdfplumber
import pymupdf


def selected_pages(page_count: int) -> list[int]:
    return sorted({0, page_count // 4, page_count // 2, (3 * page_count) // 4, page_count - 1})


def run(corpus: Path, output: Path) -> dict[str, object]:
    records = []
    for path in sorted((corpus / "raw").rglob("*.pdf")):
        with pymupdf.open(path) as document:
            pages = selected_pages(document.page_count)
            started = time.perf_counter()
            pymupdf_chars = sum(len(document[index].get_text("blocks", sort=True)) for index in pages)
            pymupdf_seconds = time.perf_counter() - started
            page_count = document.page_count
        with pdfplumber.open(path) as document:
            started = time.perf_counter()
            pdfplumber_chars = sum(len(document.pages[index].extract_text() or "") for index in pages)
            pdfplumber_seconds = time.perf_counter() - started
        records.append(
            {
                "path": path.relative_to(corpus).as_posix(),
                "page_count": page_count,
                "sample_pages_1_based": [index + 1 for index in pages],
                "pymupdf_seconds": pymupdf_seconds,
                "pymupdf_block_count": pymupdf_chars,
                "pdfplumber_seconds": pdfplumber_seconds,
                "pdfplumber_text_chars": pdfplumber_chars,
            }
        )
    pymupdf_total = sum(item["pymupdf_seconds"] for item in records)
    pdfplumber_total = sum(item["pdfplumber_seconds"] for item in records)
    payload = {
        "summary": {
            "pdf_count": len(records),
            "sampled_pages_per_pdf": 5,
            "pymupdf_total_seconds": pymupdf_total,
            "pdfplumber_total_seconds": pdfplumber_total,
            "pdfplumber_over_pymupdf_ratio": pdfplumber_total / pymupdf_total,
            "decision": "pymupdf_for_page_block_baseline_pdfplumber_for_table_audit",
            "scope_warning": "속도 비교이며 표 정확도 비교가 아니다. PDF 표는 수작업 gold로 별도 평가해야 한다.",
        },
        "samples": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload["summary"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.corpus.resolve(), args.output.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

