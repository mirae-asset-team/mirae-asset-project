"""Shared, deterministic normalization for public question text."""

from __future__ import annotations

import base64
import binascii
from datetime import date
import re
import unicodedata
from urllib.parse import unquote


PUBLIC_QUESTION_MAX_CHARS = 2_000
_DETECTION_SCAN_MAX_CHARS = PUBLIC_QUESTION_MAX_CHARS
_DECODED_SCAN_MAX_CHARS = PUBLIC_QUESTION_MAX_CHARS
_DECODE_MAX_DEPTH = 2
_DECODE_MAX_ATTEMPTS = 16
_BASE64_TOKEN = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{16,2000}={0,2}(?![A-Za-z0-9+/_=-])")
_SPACED_BASE64 = re.compile(
    r"(?<![A-Za-z0-9+/_-])(?:[A-Za-z0-9+/_-]{2,8}\s+){2,}"
    r"[A-Za-z0-9+/_-]{2,8}={0,2}(?![A-Za-z0-9+/_=-])"
)
_PROMPT_INJECTION_PHRASES = (
    "ignore previous",
    "ignore all previous",
    "disregard previous instructions",
    "system prompt",
    "developer message",
    "이전 지시를 무시",
    "지시를 무시",
    "명령을 무시",
    "시스템 프롬프트",
    "忽略之前的指令",
    "忽略先前指令",
    "系统提示",
    "系統提示",
    "以前の指示を無視",
    "前の指示を無視",
    "システムプロンプト",
    "ignora las instrucciones anteriores",
    "ignore les instructions précédentes",
    "ignoriere vorherige anweisungen",
)
_CALENDAR_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})"
    r"(?:\s*[-./월]\s*(\d{1,2})\s*일?)?(?!\d)"
)
_FISCAL_YEAR_MENTION = re.compile(r"(?<!\d)(20\d{2}|\d{2})\s*년")
_PERIOD_COMPARISON_MARKERS = (
    "비교", "대비", "보다", "차이", "증감액", "변화", "추이", "증가", "감소", "상승", "하락", "늘", "줄",
)
_CORE_FINANCIAL_METRICS = (
    "매출액", "영업수익", "영업이익", "당기순이익", "순이익", "자산총계", "부채총계", "자본총계",
)


class QuestionInputError(ValueError):
    """A stable, non-secret reason for rejecting public question input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def normalize_question_text(value: str) -> str:
    """Apply compatibility normalization and remove invisible format controls."""

    normalized = unicodedata.normalize("NFKC", str(value))
    visible = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return re.sub(r"\s+", " ", visible, flags=re.UNICODE).strip()


def _compact_detection_text(value: str) -> str:
    return "".join(
        character
        for character in normalize_question_text(value).casefold()
        if character.isalnum()
    )


_COMPACT_INJECTION_MARKERS = tuple(
    _compact_detection_text(phrase) for phrase in _PROMPT_INJECTION_PHRASES
)


def _base64_candidates(value: str) -> tuple[str, ...]:
    tokens = list(_BASE64_TOKEN.findall(value))
    tokens.extend(
        compact
        for match in _SPACED_BASE64.findall(value)
        if len(compact := re.sub(r"\s+", "", match)) >= 16
    )
    if re.fullmatch(r"[A-Za-z0-9+/_=\s-]{16,2000}", value):
        tokens.append(re.sub(r"\s+", "", value))
    return tuple(dict.fromkeys(tokens))


def _decode_base64_token(token: str) -> str | None:
    padded = token + "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return text if 0 < len(text) <= _DECODED_SCAN_MAX_CHARS else None


def _question_condition_error(text: str) -> str | None:
    calendar_mentions: list[str] = []
    for match in _CALENDAR_DATE.finditer(text):
        try:
            date(int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
        except ValueError:
            return "question_date_invalid"
        calendar_mentions.append(
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3) or 0):02d}"
        )
    if "연결" in text and "별도" in text:
        return "question_conditions_contradictory"
    years = [
        2000 + int(raw) if len(raw) == 2 else int(raw)
        for raw in _FISCAL_YEAR_MENTION.findall(text)
    ]
    present_metrics = {
        metric
        for metric in _CORE_FINANCIAL_METRICS
        if metric in text
        and not any(
            metric != longer and metric in longer and longer in text
            for longer in _CORE_FINANCIAL_METRICS
        )
    }
    metric_count = len(present_metrics)
    duplicate_period = (
        len(calendar_mentions) >= 2
        and len(set(calendar_mentions)) < len(calendar_mentions)
    ) or (
        metric_count <= 1
        and len(years) >= 2
        and len(set(years)) < len(years)
    )
    if duplicate_period and any(marker in text for marker in _PERIOD_COMPARISON_MARKERS):
        return "question_duplicate_condition_changes_meaning"
    return None


def preflight_public_question(value: object) -> str:
    """Validate and normalize a public question before routing or provider use."""

    if not isinstance(value, str):
        raise QuestionInputError("question_invalid")
    # Enforce the transport limit before removing invisible characters so they
    # cannot be used to bypass the public contract.
    if len(value) > PUBLIC_QUESTION_MAX_CHARS:
        raise QuestionInputError("question_too_long")
    normalized = normalize_question_text(value)
    if not normalized:
        raise QuestionInputError("question_invalid")
    condition_error = _question_condition_error(normalized)
    if condition_error:
        raise QuestionInputError(condition_error)
    return normalized


def detect_prompt_injection(value: str) -> bool:
    """Detect bounded encoded or obfuscated injection text without returning decoded data."""

    source = normalize_question_text(str(value)[:_DETECTION_SCAN_MAX_CHARS])
    queue: list[tuple[str, int]] = [(source, 0)]
    seen: set[str] = set()
    decode_attempts = 0
    while queue:
        view, depth = queue.pop(0)
        if view in seen:
            continue
        seen.add(view)
        compact = _compact_detection_text(view)
        if any(marker in compact for marker in _COMPACT_INJECTION_MARKERS):
            return True
        if depth >= _DECODE_MAX_DEPTH:
            continue
        if re.search(r"%[0-9A-Fa-f]{2}", view) and decode_attempts < _DECODE_MAX_ATTEMPTS:
            decode_attempts += 1
            try:
                decoded_url = unquote(view, encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, ValueError):
                decoded_url = ""
            if 0 < len(decoded_url) <= _DECODED_SCAN_MAX_CHARS and decoded_url != view:
                queue.append((decoded_url, depth + 1))
        candidates = _base64_candidates(view)
        # More encoded tokens than the bounded scanner can inspect is itself
        # an ambiguous/hostile public input. Fail closed rather than allowing
        # an attacker to choose which token remains uninspected.
        if len(candidates) > _DECODE_MAX_ATTEMPTS - decode_attempts:
            return True
        for token in candidates:
            if decode_attempts >= _DECODE_MAX_ATTEMPTS:
                break
            decode_attempts += 1
            decoded = _decode_base64_token(token)
            if decoded is not None and decoded != view:
                queue.append((decoded, depth + 1))
    return False


__all__ = [
    "PUBLIC_QUESTION_MAX_CHARS",
    "QuestionInputError",
    "detect_prompt_injection",
    "normalize_question_text",
    "preflight_public_question",
]
