"""Smoke one running HCX Function Calling endpoint without reading credentials."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request


_RCEPT_NO = re.compile(r"\d{14}\Z")


def summarize_payload(question: str, payload: dict[str, object]) -> dict[str, object]:
    tool_response = payload.get("tool_response")
    evidence_bundle = tool_response.get("evidence_bundle") if isinstance(tool_response, dict) else None
    evidence_status = (
        evidence_bundle.get("sufficiency") if isinstance(evidence_bundle, dict) else None
    )
    citations = payload.get("citations")
    citation_summary = [
        {
            "evidence_id": item.get("evidence_id"),
            "rcept_no": item.get("rcept_no"),
            "report_name": item.get("report_name"),
            "correction_role": item.get("correction_role"),
        }
        for item in citations if isinstance(item, dict)
    ] if isinstance(citations, list) else []
    return {
        "query": question,
        "status": payload.get("status"),
        "selected_tool": payload.get("tool_name"),
        "evidence_status": evidence_status,
        "answer_allowed": payload.get("answer_allowed"),
        "recommended_action": payload.get("recommended_action"),
        "citations": citation_summary,
        "answer": payload.get("answer"),
        "latency_ms": payload.get("latency_ms"),
        "warnings": payload.get("warnings"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--question", required=True)
    parser.add_argument("--expect-tool")
    parser.add_argument("--expect-status", choices=(
        "answered", "abstained", "invalid_request", "provider_unavailable", "error",
    ))
    parser.add_argument("--expect-answer-allowed", choices=("true", "false"))
    parser.add_argument("--show-full", action="store_true")
    args = parser.parse_args()
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/v1/hcx/function-answer",
        data=json.dumps({"question": args.question}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if args.expect_tool and payload.get("tool_name") != args.expect_tool:
        raise SystemExit(f"unexpected tool: {payload.get('tool_name')!r}")
    if args.expect_status and payload.get("status") != args.expect_status:
        raise SystemExit(f"unexpected status: {payload.get('status')!r}")
    if args.expect_answer_allowed is not None:
        expected = args.expect_answer_allowed == "true"
        if payload.get("answer_allowed") is not expected:
            raise SystemExit(f"unexpected answer_allowed: {payload.get('answer_allowed')!r}")
    if payload.get("status") == "answered":
        citations = payload.get("citations")
        if not isinstance(citations, list) or not citations:
            raise SystemExit("answered response has no structured citations")
        if any(
            not isinstance(item, dict)
            or _RCEPT_NO.fullmatch(str(item.get("rcept_no") or "")) is None
            for item in citations
        ):
            raise SystemExit("answered response has an invalid DART rcept_no")
    summary = summarize_payload(args.question, payload)
    print(json.dumps(payload if args.show_full else summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
