"""Grounded answer generation: deterministic fallback first, HCX only when configured."""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import replace
from typing import Any

from .agent_contracts import AnswerDraft, EvidenceBundle


UNANSWERABLE_TEXT = "검증 가능한 근거가 충분하지 않아 답변할 수 없습니다."


class DeterministicGenerator:
    def generate(self, bundle: EvidenceBundle) -> AnswerDraft:
        if not bundle.answerable or not bundle.evidence:
            return AnswerDraft(answer=UNANSWERABLE_TEXT, answerable=False, reason_codes=list(bundle.reason_codes) + ["insufficient_evidence"])
        citations = [ref.evidence_id for ref in bundle.evidence]
        numeric_values: list[str] = []
        if bundle.calculation and bundle.calculation.value is not None:
            numeric_values = [str(bundle.calculation.value)]
            answer = f"계산 결과는 {bundle.calculation.value} {bundle.calculation.unit or ''}입니다.".strip()
        elif bundle.financial_facts:
            fact = bundle.financial_facts[0]
            numeric_values = [str(fact.get("value_numeric", ""))]
            answer = f"{fact.get('account_name_raw', '해당 항목')}은(는) {fact.get('value_numeric')} {fact.get('unit_raw') or fact.get('currency') or ''}입니다.".strip()
        else:
            answer = bundle.evidence[0].text.strip() or UNANSWERABLE_TEXT
        return AnswerDraft(answer=answer, citation_ids=citations, numeric_values=numeric_values, answerable=True)


class HyperClovaGenerator:
    """Minimal HCX-005-compatible adapter with deterministic fallback.

    The adapter is opt-in: without CLOVASTUDIO_API_KEY it never makes a network call.  The
    prompt contains only retrieved evidence, and the verifier remains authoritative.
    """

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None, model: str | None = None, timeout: float = 20.0):
        self.api_key = api_key or os.getenv("CLOVASTUDIO_API_KEY")
        self.base_url = (base_url or os.getenv("CLOVASTUDIO_BASE_URL") or "https://clovastudio.stream.ntruss.com/v1/openai").rstrip("/")
        self.model = model or os.getenv("CLOVASTUDIO_MODEL") or "HCX-005"
        self.timeout = timeout
        self.fallback = DeterministicGenerator()

    def generate(self, bundle: EvidenceBundle) -> AnswerDraft:
        if not self.api_key:
            return self.fallback.generate(bundle)
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "한국어 공시 분석가. 제공된 근거만 사용하고 JSON만 반환한다."},
                {"role": "user", "content": json.dumps({
                    "question": bundle.question,
                    "evidence": [{"evidence_id": ref.evidence_id, "text": ref.text} for ref in bundle.evidence],
                    "schema": {"answer": "string", "citation_ids": ["string"], "numeric_values": ["string"], "answerable": True},
                }, ensure_ascii=False)},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, str) and content.strip().startswith("```"):
                content = content.strip().strip("`").replace("json\n", "", 1)
            parsed: dict[str, Any] = json.loads(content) if isinstance(content, str) else content
            return AnswerDraft(
                answer=str(parsed.get("answer", UNANSWERABLE_TEXT)),
                citation_ids=[str(item) for item in parsed.get("citation_ids", [])],
                numeric_values=[str(item) for item in parsed.get("numeric_values", [])],
                answerable=bool(parsed.get("answerable", True)),
            )
        except Exception:
            # A provider timeout or malformed response cannot turn into an answer.
            return replace(self.fallback.generate(bundle), reason_codes=["hcx_fallback"])
