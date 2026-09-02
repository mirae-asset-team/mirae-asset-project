"""Deterministic routing for clear disclosure questions before any LLM call."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
import os
from pathlib import Path
import re
from typing import Iterable, Mapping

from .agent_contracts import QueryPlan
from .financial_accounts import load_financial_account_catalog, normalize_account_text
from .input_hardening import (
    QuestionInputError,
    detect_prompt_injection,
    normalize_question_text,
    preflight_public_question,
)
from .query_planner import plan_query


_RECEIPT_NUMBER = re.compile(r"(?<!\d)(\d{14})(?!\d)")
_TREND_MARKERS = ("트렌드", "트랜드", "추이", "동향", "빈도", "건수 변화")
_SUMMARY_MARKERS = ("요약", "요악", "요약해", "정리해", "핵심 내용")
_SEARCH_MARKERS = ("찾아", "검색", "언급", "관련 공시", "공시 내용", "어떤 공시")
_CORRECTION_MARKERS = ("정정", "최초공시", "원공시", "변경 전", "변경 후")
_CHANGE_REASON_MARKERS = ("증가한 이유", "감소한 이유", "증가 이유", "감소 이유", "변동 이유", "왜 증가", "왜 감소")
_UNAVAILABLE_MARKERS = ("시가총액", "목표주가", "미래 주가", "주가 예측", "예상 주가")
_COMPARISON_MARKERS = ("비교", "중", "더 높은", "더 낮은", "큰 곳", "작은 곳", "어디")
_PERIOD_COMPARISON_MARKERS = (
    "비교", "대비", "보다", "차이", "증감액", "변화", "추이", "증가", "감소", "상승", "하락", "늘", "줄",
)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
_CALENDAR_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-./년]\s*(\d{1,2})"
    r"(?:\s*[-./월]\s*(\d{1,2})\s*일?)?(?!\d)"
)
_FISCAL_YEAR_MENTION = re.compile(r"(?<!\d)(20\d{2}|\d{2})\s*년")
_KOREAN_SUFFIXES = ("으로", "에서", "에게", "까지", "부터", "처럼", "보다", "의", "은", "는", "이", "가", "을", "를", "로")


def _distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return False
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    edits = 0
    for character in longer:
        if short_index < len(shorter) and shorter[short_index] == character:
            short_index += 1
        else:
            edits += 1
            if edits > 1:
                return False
    return True


def _core_token(token: str) -> str:
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[:-len(suffix)]
    return token


@dataclass(frozen=True, slots=True)
class QuestionRoute:
    kind: str
    reason: str
    tool_name: str | None = None
    arguments: Mapping[str, object] = field(default_factory=dict)
    response_mode: str = "provider"
    message: str | None = None
    normalized_question: str | None = None
    corrections: tuple[str, ...] = ()
    workflow: str = "single"
    metric_kind: str = "SEARCH"
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in {"tool", "clarification", "unavailable"}:
            raise ValueError("question_route_kind_invalid")
        if self.kind == "tool" and not self.tool_name:
            raise ValueError("question_route_tool_missing")
        if self.response_mode not in {"deterministic", "provider"}:
            raise ValueError("question_route_response_mode_invalid")
        object.__setattr__(self, "arguments", dict(self.arguments))
        object.__setattr__(self, "corrections", tuple(self.corrections))
        object.__setattr__(self, "context", dict(self.context))


class DeterministicQuestionRouter:
    """Route only high-confidence Korean question shapes; otherwise return ``None``."""

    def __init__(
        self,
        company_candidates: Iterable[str],
        *,
        company_aliases: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.company_candidates = tuple(dict.fromkeys(str(item) for item in company_candidates if str(item)))
        self.company_aliases = self._load_company_aliases(company_aliases)
        catalog = load_financial_account_catalog()
        self.account_catalog = catalog
        self.account_surfaces = tuple(
            (normalize_account_text(surface), account.canonical_id, account.label_ko)
            for account in catalog.accounts
            for surface in (account.label_ko, *account.aliases)
            if len(normalize_account_text(surface)) >= 4
        )

    def _load_company_aliases(
        self, configured: Mapping[str, Iterable[str]] | None,
    ) -> dict[str, tuple[str, ...]]:
        if configured is not None:
            raw = configured
        else:
            raw_aliases: dict[str, list[str]] = {}
            project_root = Path(__file__).resolve().parents[2]
            universe_path = project_root / "data" / "derived" / "financial_company_universe.json"
            if universe_path.is_file():
                with universe_path.open(encoding="utf-8") as handle:
                    universe = json.load(handle)
                rows = universe.get("companies", []) if isinstance(universe, Mapping) else []
                for row in rows:
                    if not isinstance(row, Mapping) or not row.get("issuer_name"):
                        continue
                    canonical = str(row["issuer_name"])
                    surfaces = raw_aliases.setdefault(canonical, [])
                    for surface in (
                        canonical,
                        row.get("listed_name"),
                        *(row.get("aliases", []) if isinstance(row.get("aliases"), list) else []),
                        row.get("stock_code"),
                    ):
                        if surface and str(surface) not in surfaces:
                            surfaces.append(str(surface))

            config_dir = Path(os.environ.get("DISCLOSURE_CONFIG_DIR") or project_root / "config")
            path = config_dir / "company_aliases.json"
            if path.is_file():
                with path.open(encoding="utf-8") as handle:
                    payload = json.load(handle)
                rows = payload.get("companies", []) if isinstance(payload, Mapping) else []
                for row in rows:
                    if not isinstance(row, Mapping) or not row.get("canonical"):
                        continue
                    canonical = str(row["canonical"])
                    surfaces = raw_aliases.setdefault(canonical, [])
                    for alias in row.get("aliases", []):
                        if alias and str(alias) not in surfaces:
                            surfaces.append(str(alias))
            raw = raw_aliases
        allowed = set(self.company_candidates)
        return {
            str(canonical): tuple(dict.fromkeys(str(alias) for alias in aliases if str(alias)))
            for canonical, aliases in raw.items()
            if str(canonical) in allowed
        }

    def _normalize_company_aliases(self, text: str) -> tuple[str, tuple[str, ...]]:
        normalized = text
        corrections: list[str] = []
        surfaces = sorted(
            (
                (normalize_question_text(alias).casefold(), canonical)
                for canonical, aliases in self.company_aliases.items()
                for alias in aliases
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for alias, canonical in surfaces:
            if not alias:
                continue
            if alias.isdigit():
                pattern = re.compile(rf"(?<!\d){re.escape(alias)}(?!\d)")
            elif any(character.isascii() and character.isalnum() for character in alias):
                pattern = re.compile(
                    rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
                    re.IGNORECASE,
                )
            else:
                pattern = re.compile(re.escape(alias), re.IGNORECASE)
            matched = pattern.search(normalized)
            if matched is not None:
                observed = matched.group(0)
                normalized = pattern.sub(canonical, normalized)
                if observed != canonical:
                    corrections.append(f"alias:{observed.casefold()}->{canonical}")
        return normalized, tuple(dict.fromkeys(corrections))

    def _collapse_spaced_companies(self, text: str) -> tuple[str, tuple[str, ...]]:
        """Collapse whitespace inserted inside a registered company surface.

        Token-based typo repair cannot see '삼 성 전 자' because every token
        shrinks below the minimum match length, so spaced variants must be
        repaired on the raw text before alias and typo normalization.
        """
        normalized = text
        corrections: list[str] = []
        surfaces = sorted(
            {
                surface
                for canonical, aliases in self.company_aliases.items()
                for surface in (canonical, *aliases)
            } | set(self.company_candidates),
            key=len,
            reverse=True,
        )
        for company in surfaces:
            if len(company.replace(" ", "")) < 3 or company in normalized:
                continue
            pattern = re.compile(r"[ \t]*".join(re.escape(character) for character in company if character != " "))
            match = pattern.search(normalized)
            if match is None or " " not in match.group(0) and "\t" not in match.group(0):
                continue
            normalized = normalized[: match.start()] + company + normalized[match.end():]
            corrections.append(f"spacing:{match.group(0)}->{company}")
        return normalized, tuple(corrections)

    def _companies_in_text(self, text: str) -> tuple[str, ...]:
        matches = [
            (text.find(company), -len(company), company)
            for company in self.company_candidates
            if company in text
        ]
        return tuple(company for _, _, company in sorted(matches))

    @staticmethod
    def _replace_unique_typo(
        text: str,
        candidates: Iterable[tuple[str, str, str]],
    ) -> tuple[str, str | None]:
        candidate_rows = tuple(candidates)
        exact_surfaces = {surface for surface, _, _ in candidate_rows}
        matches: list[tuple[str, str, str]] = []
        for raw_token in _TOKEN.findall(text):
            core = _core_token(raw_token)
            normalized_core = normalize_account_text(core)
            if len(normalized_core) < 4:
                continue
            # A real registered name must always win over a nearby one-edit
            # candidate.  For example, Samsung Electronics (삼성전자) and
            # Samsung Electro-Mechanics (삼성전기) differ by one character.
            if normalized_core in exact_surfaces:
                continue
            for surface, identity, replacement in candidate_rows:
                if _distance_at_most_one(normalized_core, surface):
                    matches.append((core, identity, replacement))
        identities = {identity for _, identity, _ in matches}
        replacements = {(core, replacement) for core, _, replacement in matches}
        if len(identities) != 1 or len(replacements) != 1:
            return text, None
        core, replacement = next(iter(replacements))
        return text.replace(core, replacement, 1), f"{core}->{replacement}"

    def _normalize_typos(self, text: str) -> tuple[str, tuple[str, ...]]:
        normalized, spacing_corrections = self._collapse_spaced_companies(text)
        normalized, alias_corrections = self._normalize_company_aliases(normalized)
        company_surfaces = tuple(
            (normalize_account_text(company), company, company)
            for company in self.company_candidates
            if len(normalize_account_text(company)) >= 4
        )
        normalized, company_correction = self._replace_unique_typo(normalized, company_surfaces)
        existing_resolution = self.account_catalog.resolve(normalized)
        if existing_resolution.status == "unknown":
            normalized, account_correction = self._replace_unique_typo(normalized, self.account_surfaces)
        else:
            account_correction = None
        return normalized, tuple([
            *spacing_corrections,
            *alias_corrections,
            *(item for item in (company_correction, account_correction) if item),
        ])

    def _structured_metrics(self, text: str, plan: QueryPlan) -> tuple[tuple[str, str], ...]:
        if (
            plan.account_status == "resolved"
            and plan.account_support_level == "structured"
            and plan.account_id
            and plan.account_terms
        ):
            return ((plan.account_id, plan.account_terms[0]),)
        if (
            plan.account_status != "ambiguous"
            or plan.account_match_type == "ambiguous_expression"
            or len(plan.account_candidates) < 2
        ):
            return ()

        compact = normalize_account_text(text)
        found: list[tuple[int, str, str]] = []
        for account_id in plan.account_candidates:
            account = self.account_catalog.by_id.get(account_id)
            if account is None or account.support_level != "structured":
                return ()
            positions = [
                compact.find(normalize_account_text(surface))
                for surface in (account.label_ko, *account.aliases)
            ]
            positions = [position for position in positions if position >= 0]
            if not positions:
                return ()
            found.append((min(positions), account.canonical_id, account.label_ko))
        return tuple((account_id, label) for _, account_id, label in sorted(found))

    @staticmethod
    def _has_invalid_calendar_date(text: str) -> bool:
        for match in _CALENDAR_DATE.finditer(text):
            year, month = int(match.group(1)), int(match.group(2))
            day = int(match.group(3) or 1)
            try:
                date(year, month, day)
            except ValueError:
                return True
        return False

    @staticmethod
    def _has_meaning_changing_duplicate_year(text: str, metric_count: int) -> bool:
        years = [
            2000 + int(raw) if len(raw) == 2 else int(raw)
            for raw in _FISCAL_YEAR_MENTION.findall(text)
        ]
        return (
            metric_count <= 1
            and len(years) >= 2
            and len(set(years)) < len(years)
            and any(marker in text for marker in _PERIOD_COMPARISON_MARKERS)
        )

    def route(self, question: str) -> QuestionRoute | None:
        original = str(question).strip()
        try:
            normalized_input = preflight_public_question(question)
        except QuestionInputError as exc:
            messages = {
                "question_date_invalid": "달력에 존재하는 날짜로 다시 입력해 주세요.",
                "question_conditions_contradictory": "연결 또는 별도 재무제표 중 하나를 지정해 주세요.",
                "question_duplicate_condition_changes_meaning": "같은 비교 기간이 중복되었습니다. 서로 다른 기간을 지정해 주세요.",
            }
            return QuestionRoute(
                "clarification" if exc.code in messages else "unavailable",
                exc.code,
                response_mode="deterministic",
                message=messages.get(exc.code, "질문 형식이 올바르지 않습니다."),
                metric_kind="AMBIGUOUS" if exc.code in messages else "UNAVAILABLE",
            )
        text, corrections = self._normalize_typos(normalized_input)
        route_context = {
            "normalized_question": text if text != original else None,
            "corrections": corrections,
        }
        if detect_prompt_injection(text):
            return QuestionRoute(
                "unavailable",
                "prompt_injection_detected",
                response_mode="deterministic",
                message="질문에 실행 지시 변경 요청이 포함되어 처리할 수 없습니다.",
                metric_kind="UNAVAILABLE",
                **route_context,
            )
        if self._has_invalid_calendar_date(text):
            return QuestionRoute(
                "clarification",
                "question_date_invalid",
                response_mode="deterministic",
                message="달력에 존재하는 날짜로 다시 입력해 주세요.",
                metric_kind="AMBIGUOUS",
                **route_context,
            )
        if "연결" in text and "별도" in text:
            return QuestionRoute(
                "clarification",
                "question_conditions_contradictory",
                response_mode="deterministic",
                message="연결 또는 별도 재무제표 중 하나를 지정해 주세요.",
                metric_kind="AMBIGUOUS",
                **route_context,
            )
        plan = plan_query(text, company_candidates=self.company_candidates)
        companies = self._companies_in_text(text)
        period_requirements: list[dict[str, str]] = []
        for period in plan.target_periods:
            start = str(period.get("start") or "")
            end = str(period.get("end") or "")
            instant = str(period.get("instant") or "")
            if start.endswith("-01-01") and end == f"{start[:4]}-12-31":
                label = start[:4]
                requirement = {"period": label}
            elif instant:
                label = instant
                requirement = {"period": label, "instant_date": instant}
            elif end:
                label = end
                requirement = {"period": label, "end_date": end}
                if start:
                    requirement["start_date"] = start
            else:
                continue
            if requirement not in period_requirements:
                period_requirements.append(requirement)
        periods = tuple(item["period"] for item in period_requirements)
        metrics = self._structured_metrics(text, plan)

        if self._has_meaning_changing_duplicate_year(text, len(metrics)):
            return QuestionRoute(
                "clarification",
                "question_duplicate_condition_changes_meaning",
                response_mode="deterministic",
                message="같은 비교 기간이 중복되었습니다. 서로 다른 기간을 지정해 주세요.",
                metric_kind="AMBIGUOUS",
                **route_context,
            )

        if plan.account_status == "ambiguous" and not metrics and plan.account_warning:
            return QuestionRoute(
                "clarification",
                "financial_account_ambiguous",
                response_mode="deterministic",
                message=plan.account_warning,
                metric_kind="AMBIGUOUS",
                context={"candidates": list(plan.account_candidates)},
                **route_context,
            )

        if plan.question_type == "out_of_scope" or any(marker in text for marker in _UNAVAILABLE_MARKERS):
            return QuestionRoute(
                "unavailable",
                "question_outside_disclosure_scope",
                response_mode="deterministic",
                message="이 서비스는 DART 공시 근거를 다루므로 주가 예측·목표주가·미래 전망은 답변 범위에 포함되지 않습니다.",
                metric_kind="UNAVAILABLE",
                context={"available_range": "DART 공시, 구조화 재무수치, 공시 검색·요약·정정 관계"},
                **route_context,
            )

        if (
            metrics
            and plan.company is not None
            and (len(companies) >= 2 or len(periods) >= 2 or len(metrics) >= 2)
        ):
            required_companies = list(companies) if companies else [str(plan.company)]
            required_periods: list[dict[str, str | None]] = (
                [dict(item) for item in period_requirements]
                if period_requirements
                else [{"period": None}]
            )
            requirements = [
                {
                    "company": company,
                    **period,
                    "account": metric,
                }
                for company in required_companies
                for period in required_periods
                for _, metric in metrics
            ]
            first_requirement = requirements[0]
            arguments: dict[str, object] = {
                "company": first_requirement["company"],
                "account": first_requirement["account"],
                "correction_policy": plan.correction_policy,
                "top_k": 1,
            }
            first_period = first_requirement["period"]
            if first_period:
                if first_requirement.get("instant_date"):
                    arguments["instant_date"] = first_requirement["instant_date"]
                elif first_requirement.get("start_date") or first_requirement.get("end_date"):
                    if first_requirement.get("start_date"):
                        arguments["start_date"] = first_requirement["start_date"]
                    if first_requirement.get("end_date"):
                        arguments["end_date"] = first_requirement["end_date"]
                else:
                    arguments["start_date"] = f"{first_period}-01-01"
                    arguments["end_date"] = f"{first_period}-12-31"
            if plan.scope:
                arguments["scope"] = plan.scope
            return QuestionRoute(
                "tool",
                "financial_multi_axis_comparison",
                "get_financial_facts",
                arguments,
                workflow="financial_comparison",
                metric_kind="DERIVED",
                context={
                    "companies": required_companies,
                    "period": periods[0] if len(periods) == 1 else None,
                    "periods": list(periods),
                    "metric": metrics[0][1] if len(metrics) == 1 else None,
                    "metric_id": metrics[0][0] if len(metrics) == 1 else None,
                    "metrics": [metric for _, metric in metrics],
                    "metric_ids": [account_id for account_id, _ in metrics],
                    "intent": "financial_comparison",
                    "requirements": requirements,
                    "required_evidence": "validated_structured_fact_per_company_and_period",
                },
                **route_context,
            )

        if any(marker in text for marker in _TREND_MARKERS):
            if plan.period_start and plan.period_end:
                arguments: dict[str, object] = {
                    "start_date": plan.period_start,
                    "end_date": plan.period_end,
                    "granularity": "month",
                    "representative_limit": 10,
                }
                if plan.company:
                    arguments["company"] = plan.company
                return QuestionRoute(
                    "tool", "explicit_trend_question", "analyze_disclosure_trend", arguments,
                    workflow="trend_with_content",
                    metric_kind="SEARCH",
                    context={"question": text},
                    **route_context,
                )
            return None

        receipt = _RECEIPT_NUMBER.search(text)
        if receipt and any(marker in text for marker in _CORRECTION_MARKERS):
            return QuestionRoute(
                "tool",
                "explicit_correction_receipt",
                "get_correction_lineage",
                {"filing_id": receipt.group(1)},
                metric_kind="SEARCH",
                **route_context,
            )

        if plan.company and any(marker in text for marker in _CORRECTION_MARKERS):
            return QuestionRoute(
                "tool",
                "correction_question_without_receipt",
                "search_disclosures",
                {
                    "question": text,
                    "company": plan.company,
                    "correction_policy": "both",
                    "top_k": 10,
                },
                workflow="correction_search_then_lineage",
                metric_kind="SEARCH",
                **route_context,
            )

        if (
            plan.company
            and plan.account_status == "resolved"
            and plan.account_support_level == "structured"
            and plan.account_terms
            and any(marker in text for marker in _CHANGE_REASON_MARKERS)
        ):
            arguments = {
                "company": plan.company,
                "account": plan.account_terms[0],
                "correction_policy": plan.correction_policy,
                "top_k": 2,
            }
            if plan.scope:
                arguments["scope"] = plan.scope
            return QuestionRoute(
                "tool",
                "financial_change_reason",
                "get_financial_facts",
                arguments,
                workflow="financial_change_reason",
                metric_kind="DERIVED",
                context={
                    "question": text,
                    "company": plan.company,
                    "target_start_date": plan.period_start,
                    "target_end_date": plan.period_end,
                },
                **route_context,
            )

        if (
            plan.company
            and plan.account_status == "resolved"
            and plan.account_support_level in {"structured", "derived"}
            and plan.operation in {"growth_rate", "difference", "ratio", "sum"}
            and (plan.account_terms or plan.required_account_ids)
        ):
            account_term = plan.account_terms[0] if plan.account_support_level == "structured" else None
            if plan.account_support_level == "derived" and len(plan.required_account_ids) == 1:
                source = self.account_catalog.by_id.get(plan.required_account_ids[0])
                account_term = source.label_ko if source is not None else None
            if not account_term:
                return None
            arguments = {
                "company": plan.company,
                "account": account_term,
                "correction_policy": plan.correction_policy,
                "top_k": max(2, min(plan.latest_period_count, 20)),
            }
            if plan.period_start:
                arguments["start_date"] = plan.period_start
            if plan.period_end:
                arguments["end_date"] = plan.period_end
            if plan.scope:
                arguments["scope"] = plan.scope
            return QuestionRoute(
                "tool",
                "structured_financial_calculation",
                "get_financial_facts",
                arguments,
                workflow="financial_derived",
                metric_kind="DERIVED",
                context={"operation": plan.operation},
                **route_context,
            )

        if (
            plan.company
            and plan.account_status == "resolved"
            and plan.account_support_level == "structured"
            and plan.operation == "lookup"
            and plan.account_terms
        ):
            arguments = {
                "company": plan.company,
                "account": plan.account_terms[0],
                "correction_policy": plan.correction_policy,
                "top_k": max(1, min(plan.latest_period_count, 20)),
            }
            if plan.period_start:
                arguments["start_date"] = plan.period_start
            if plan.period_end:
                arguments["end_date"] = plan.period_end
            if plan.scope:
                arguments["scope"] = plan.scope
            return QuestionRoute(
                "tool",
                "structured_financial_lookup",
                "get_financial_facts",
                arguments,
                metric_kind="DIRECT",
                **route_context,
            )

        common_search: dict[str, object] = {
            "question": text,
            "correction_policy": plan.correction_policy,
        }
        if plan.company:
            common_search["company"] = plan.company
        if plan.period_start:
            common_search["start_date"] = plan.period_start
        if plan.period_end:
            common_search["end_date"] = plan.period_end

        if any(marker in text for marker in _SUMMARY_MARKERS):
            return QuestionRoute(
                "tool",
                "explicit_summary_question",
                "build_summary_context",
                {**common_search, "top_k": 12, "max_chars": 8_000},
                workflow="document_summary_check" if "사업보고서" in text else "single",
                metric_kind="SEARCH",
                context={"report_type": "사업보고서"} if "사업보고서" in text else {},
                **route_context,
            )

        if plan.account_support_level == "retrieval_only" and plan.company:
            quarter_match = re.search(r"([1-4])\s*분기", text)
            quarter = int(quarter_match.group(1)) if quarter_match else None
            fiscal_year = int(plan.period_end[:4]) if plan.period_end else None
            account_resolution = self.account_catalog.resolve(plan.account_terms[0]) if plan.account_terms else None
            account = (
                self.account_catalog.by_id.get(account_resolution.canonical_id)
                if account_resolution is not None and account_resolution.canonical_id
                else None
            )
            annual_period = bool(
                quarter is None
                and fiscal_year is not None
                and plan.period_start == f"{fiscal_year}-01-01"
                and plan.period_end == f"{fiscal_year}-12-31"
            )
            deterministic_supported = bool(
                fiscal_year is not None
                and plan.correction_policy == "current"
                and plan.operation == "lookup"
                and len(plan.target_periods) == 1
                and account is not None
                and account.statement_type == "income_statement"
                and account.expected_value_type == "monetary"
                and (annual_period or quarter == 1)
            )
            statement_search = dict(common_search)
            if deterministic_supported:
                statement_search.pop("start_date", None)
                statement_search.pop("end_date", None)
                statement_search.update({
                    "account": plan.account_terms[0] if plan.account_terms else "",
                    "fiscal_year": fiscal_year,
                    "period_kind": "quarter" if quarter is not None else "annual",
                })
                if quarter is not None:
                    statement_search["quarter"] = quarter
                if plan.scope:
                    statement_search["scope"] = plan.scope
            return QuestionRoute(
                "tool",
                "retrieval_only_financial_account",
                "build_summary_context",
                {**statement_search, "top_k": 12, "max_chars": 8_000},
                workflow="financial_statement_metric",
                metric_kind="SEARCH",
                context={
                    "metric": plan.account_terms[0] if plan.account_terms else None,
                    "fiscal_year": str(fiscal_year) if fiscal_year is not None else None,
                    "period_kind": "quarter" if quarter is not None else "annual",
                    "quarter": quarter,
                },
                **route_context,
            )

        if plan.question_type == "event_numeric" and plan.company:
            return QuestionRoute(
                "tool",
                "event_search_fallback",
                "search_disclosures",
                {**common_search, "top_k": 10},
                metric_kind="SEARCH",
                **route_context,
            )

        if any(marker in text for marker in _SEARCH_MARKERS):
            return QuestionRoute(
                "tool",
                "explicit_disclosure_search",
                "search_disclosures",
                {**common_search, "top_k": 10},
                metric_kind="SEARCH",
                **route_context,
            )

        if plan.account_status == "unsupported":
            return QuestionRoute(
                "unavailable",
                "financial_metric_unavailable",
                response_mode="deterministic",
                message=plan.account_warning or "현재 구조화 재무계정 범위에서 지원하지 않는 지표입니다.",
                metric_kind="UNAVAILABLE",
                context={"available_range": "financial_account_catalog.json의 structured/retrieval_only 계정"},
                **route_context,
            )
        return None


__all__ = ["DeterministicQuestionRouter", "QuestionRoute"]
