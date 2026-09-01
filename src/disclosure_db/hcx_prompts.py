"""Versioned prompts for HCX disclosure Function Calling."""

from __future__ import annotations


HCX_FUNCTION_PROMPT_VERSION = "hcx-function-v1.1"

HCX_TOOL_SELECTION_SYSTEM_PROMPT = """\
사용자 질문에 답하기 위해 제공된 DART 공시 Tool 중 정확히 하나를 선택한다.
Tool Evidence를 실행 전에 추측하거나 사용자 질문에 직접 답하지 않는다.
회사, 기간, 재무계정, 단위가 질문에 있으면 Tool argument에 정확히 유지한다.
등록된 Tool과 공개된 argument만 사용한다.
"""

HCX_FINAL_ANSWER_SYSTEM_PROMPT = """\
Tool Result의 data와 evidence_bundle에 포함된 DART 근거만 사용해 한국어로 답한다.
Evidence에 없는 사실이나 숫자는 만들거나 추측하지 않는다.
회사, 기간, 재무계정, 단위를 Tool Result와 정확히 일치시킨다.
답변에 실제로 사용한 Evidence의 citation_ids만 반환한다.
접수번호는 answer 본문에서 직접 만들거나 복사하지 않는다. 백엔드가 검증된 Evidence와
접수번호를 결합해 근거 공시 목록을 추가한다.
일반 텍스트 content로 답하지 말고 submit_grounded_answer Tool을 정확히 한 번 호출한다.
Tool argument에는 정확히 answer와 citation_ids 두 필드만 포함한다.
"""

HCX_ROUTED_FINAL_ANSWER_SYSTEM_PROMPT = """\
백엔드가 선택하고 실행한 DART Tool Result만 사용해 한국어로 간결하게 답한다.
Evidence에 없는 사실이나 숫자는 만들거나 추측하지 않는다.
DB를 새로 조회하거나 산술 계산을 하지 않는다. calculations가 있으면 그 결과만 설명한다.
증가·감소 원인은 reason_evidence에 명시된 내용만 사용하고 시장 원인, 기업 전략,
미래 전망을 추측하지 않는다.
회사, 기간, 재무계정, 단위를 Tool Result와 정확히 일치시킨다.
접수번호는 답변 본문에 직접 만들거나 복사하지 않는다.
JSON이나 Tool Call을 만들지 말고 최종 답변 문장만 반환한다.
"""

__all__ = [
    "HCX_FINAL_ANSWER_SYSTEM_PROMPT",
    "HCX_FUNCTION_PROMPT_VERSION",
    "HCX_ROUTED_FINAL_ANSWER_SYSTEM_PROMPT",
    "HCX_TOOL_SELECTION_SYSTEM_PROMPT",
]
