"""Deterministic routing for clear disclosure questions before any LLM call."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Iterable, Mapping

from .financial_accounts import load_financial_account_catalog, normalize_account_text
from .query_planner import plan_query


_RECEIPT_NUMBER = re.compile(r"(?<!\d)(\d{14})(?!\d)")
_TREND_MARKERS = ("트렌드", "트랜드", "추이", "동향", "빈도", "건수 변화")
_SUMMARY_MARKERS = ("요약", "요악", "요약해", "정리해", "핵심 내용")
_SEARCH_MARKERS = ("찾아", "검색", "언급", "관련 공시", "공시 내용", "어떤 공시")
_CORRECTION_MARKERS = ("정정", "최초공시", "원공시", "변경 전", "변경 후")
_INCREASE_REASON_MARKERS = (
    "증가한 이유", "증가 이유", "왜 증가", "증가했는데", "증가한 배경", "증가 배경",
)
_DECREASE_REASON_MARKERS = (
    "감소한 이유", "감소 이유", "왜 감소", "감소했는데", "감소한 배경", "감소 배경",
)
_CHANGE_REASON_MARKERS = (*_INCREASE_REASON_MARKERS, *_DECREASE_REASON_MARKERS, "변동 이유")
_EVENT_DISCLOSURE_MARKERS = (
    "대량보유", "자기주식", "유상증자", "무상증자", "전환사채", "조건부자본증권",
    "시설투자", "공급계약", "회사합병", "합병 결정", "회사분할", "분할 결정",
    "감자 결정", "주식교환", "투자판단", "소송",
)
_STOCK_SPLIT_MARKERS = ("액면분할", "주식분할")
_STOCK_SPLIT_REPORT_PATTERNS = ("주식분할결정", "액면분할결정")
_UNAVAILABLE_MARKERS = ("시가총액", "목표주가", "미래 주가", "주가 예측", "예상 주가")
_FOREIGN_CURRENCY_MARKERS = (
    "미국 달러", "달러", "usd", "유로", "eur", "엔화", "일본 엔", "jpy", "위안", "cny",
)
# Solicitation phrasings ask for a trading decision rather than a disclosure
# fact. Routing them through the provider cost ~9s and still ended in a
# refusal, so the refusal is made deterministic and immediate.
_ADVICE_MARKERS = (
    "사도 될까", "사도 되나", "사도 돼", "매수해도", "매도해도", "팔아도 될까",
    "投資", "투자해도", "사는 게 좋을", "사는게 좋을", "사는 게 맞", "사는게 맞",
    "살까요", "팔까요",
    "말려야", "추천해", "추천할", "유망한가", "괜찮은 종목", "들어가도",
)
_NONPUBLIC_INFORMATION_MARKERS = (
    "공시 전인", "공시되지 않은", "아직 공시되지", "아직 공개되지", "미공개", "내부정보",
)
# Document-shaped questions that name no financial account. These carry a
# company and a disclosure topic, so the search tool is the grounded route.
_DOCUMENT_TOPIC_MARKERS = (
    "배당", "연구개발", "위험요인", "리스크", "사업 내용", "사업내용", "주요 사업",
    "경영진", "임원", "종업원", "직원 수", "계열회사", "지배구조", "주주",
    "설비투자", "생산능력", "매출 구성", "제품", "시장 점유", "소송", "특허",
)
_COMPARISON_MARKERS = ("비교", "중", "더 높은", "더 낮은", "큰 곳", "작은 곳", "어디")
_PERIOD_COMPARISON_MARKERS = (
    "비교", "대비", "보다", "변화", "추이", "증가", "감소", "상승", "하락", "늘", "줄",
)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")
_KOREAN_SUFFIXES = ("으로", "에서", "에게", "까지", "부터", "처럼", "보다", "의", "은", "는", "이", "가", "을", "를", "로")


def _claimed_change_direction(text: str) -> str | None:
    if any(marker in text for marker in _INCREASE_REASON_MARKERS):
        return "increase"
    if any(marker in text for marker in _DECREASE_REASON_MARKERS):
        return "decrease"
    return None


def _requested_output_unit(text: str) -> str | None:
    """Return only explicit KRW display-unit requests, most specific first."""

    if re.search(r"조(?:원)?\s*단위", text):
        return "jo"
    if re.search(r"억(?:원)?\s*단위", text):
        return "eok"
    if re.search(r"(?<![가-힣A-Za-z0-9])원\s*단위", text):
        return "won"
    return None


def _required_event_report_patterns(text: str) -> tuple[str, ...]:
    if any(marker in text for marker in _STOCK_SPLIT_MARKERS):
        return _STOCK_SPLIT_REPORT_PATTERNS
    return ()


def _requests_foreign_currency_conversion(text: str) -> bool:
    lowered = text.casefold()
    return "환산" in text and any(marker in lowered for marker in _FOREIGN_CURRENCY_MARKERS)


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
            config_dir = Path(os.environ.get("DISCLOSURE_CONFIG_DIR") or Path(__file__).resolve().parents[2] / "config")
            path = config_dir / "company_aliases.json"
            if not path.is_file():
                return {}
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
            rows = payload.get("companies", []) if isinstance(payload, Mapping) else []
            raw = {
                str(row["canonical"]): tuple(str(item) for item in row.get("aliases", []))
                for row in rows
                if isinstance(row, Mapping) and row.get("canonical")
            }
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
                (alias, canonical)
                for canonical, aliases in self.company_aliases.items()
                for alias in aliases
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for alias, canonical in surfaces:
            if alias in normalized:
                normalized = normalized.replace(alias, canonical)
                corrections.append(f"alias:{alias}->{canonical}")
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

    def route(self, question: str) -> QuestionRoute | None:
        original = question.strip()
        text, corrections = self._normalize_typos(original)
        plan = plan_query(text, company_candidates=self.company_candidates)
        companies = self._companies_in_text(text)
        periods = tuple(dict.fromkeys(
            str(period.get("end") or period.get("instant") or "")[:4]
            for period in plan.target_periods
            if str(period.get("end") or period.get("instant") or "")[:4]
        ))
        route_context = {
            "normalized_question": text if corrections else None,
            "corrections": corrections,
        }

        if (
            plan.company
            and plan.account_status == "ambiguous"
            and any(marker in text for marker in ("각각", "모두", "둘 다"))
            and 2 <= len(plan.account_candidates) <= 4
            and len(periods) <= 1
        ):
            # "A와 B를 각각 알려주세요" names every account explicitly, so the
            # multi-account executor answers each one instead of asking the
            # user to pick a single account.
            catalog_accounts = self.account_catalog.by_id
            candidates = [catalog_accounts.get(str(item)) for item in plan.account_candidates]
            if all(item is not None and item.support_level == "structured" for item in candidates):
                period = periods[0] if periods else None
                requirements = [
                    {
                        "company": str(plan.company),
                        "period": period,
                        "account": item.label_ko,
                        "account_id": item.canonical_id,
                    }
                    for item in candidates
                ]
                account_ids = {item.canonical_id for item in candidates}
                accounting_identity = account_ids == {
                    "total_assets", "total_liabilities", "total_equity",
                } and any(marker in text for marker in ("합", "일치", "회계등식"))
                arguments = {
                    "company": str(plan.company),
                    "account": requirements[0]["account"],
                    "correction_policy": plan.correction_policy,
                    "top_k": 1,
                }
                if period:
                    arguments["start_date"] = f"{period}-01-01"
                    arguments["end_date"] = f"{period}-12-31"
                if plan.scope:
                    arguments["scope"] = plan.scope
                return QuestionRoute(
                    "tool",
                    "financial_multi_metric_request",
                    "get_financial_facts",
                    arguments,
                    workflow="financial_comparison",
                    metric_kind="DIRECT",
                    context={
                        "companies": [str(plan.company)],
                        "period": period,
                        "periods": [period] if period else [],
                        "metric": None,
                        "metrics": [item.label_ko for item in candidates],
                        "intent": "financial_multi_metric",
                        "requirements": requirements,
                        "derived_operation": "accounting_identity" if accounting_identity else None,
                        "required_evidence": "validated_structured_fact_per_account",
                    },
                    **route_context,
                )

        if plan.account_status == "ambiguous" and plan.account_warning:
            return QuestionRoute(
                "clarification",
                "financial_account_ambiguous",
                response_mode="deterministic",
                message=plan.account_warning,
                metric_kind="AMBIGUOUS",
                context={"candidates": list(plan.account_candidates)},
                **route_context,
            )

        if any(marker in text for marker in _ADVICE_MARKERS):
            return QuestionRoute(
                "unavailable",
                "investment_solicitation_out_of_scope",
                response_mode="deterministic",
                message="이 서비스는 DART 공시 근거를 확인해 드리는 곳으로, 매수·매도 판단이나 투자 권유는 하지 않습니다. 공시에 기재된 재무수치나 공시 내용은 알려드릴 수 있습니다.",
                metric_kind="UNAVAILABLE",
                context={"available_range": "DART 공시, 구조화 재무수치, 공시 검색·요약·정정 관계"},
                **route_context,
            )

        if _requests_foreign_currency_conversion(text):
            return QuestionRoute(
                "unavailable",
                "foreign_currency_conversion_requires_external_rate",
                response_mode="deterministic",
                message=(
                    "DART 공시 근거에는 환율의 기준 시점과 출처가 없어 외화 환산값을 "
                    "검증할 수 없습니다. 공시에 기재된 원화 수치는 확인할 수 있습니다."
                ),
                metric_kind="UNAVAILABLE",
                context={"available_range": "DART 공시에 기재된 원화 재무수치"},
                **route_context,
            )

        if any(marker in text for marker in _NONPUBLIC_INFORMATION_MARKERS):
            return QuestionRoute(
                "unavailable",
                "nonpublic_information_unavailable",
                response_mode="deterministic",
                message=(
                    "아직 공시되지 않은 정보나 내부정보는 확인하거나 제공할 수 없습니다. "
                    "공개된 DART 공시만 근거로 답변할 수 있습니다."
                ),
                metric_kind="UNAVAILABLE",
                context={"available_range": "공개된 DART 공시"},
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

        multi_company_comparison = (
            len(companies) >= 2 and any(marker in text for marker in _COMPARISON_MARKERS)
        )
        multi_period_comparison = (
            plan.company is not None
            and len(periods) >= 2
            and any(marker in text for marker in _PERIOD_COMPARISON_MARKERS)
        )
        if (
            (multi_company_comparison or multi_period_comparison)
            and plan.account_status == "resolved"
            and plan.account_support_level == "structured"
            and plan.account_terms
        ):
            required_companies = list(companies) if companies else [str(plan.company)]
            required_periods: list[str | None] = list(periods) if periods else [None]
            requirements = [
                {
                    "company": company,
                    "period": period,
                    "account": plan.account_terms[0],
                }
                for company in required_companies
                for period in required_periods
            ]
            first_requirement = requirements[0]
            arguments: dict[str, object] = {
                "company": first_requirement["company"],
                "account": plan.account_terms[0],
                "correction_policy": plan.correction_policy,
                "top_k": 1,
            }
            first_period = first_requirement["period"]
            if first_period:
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
                    "metric": plan.account_terms[0],
                    "metric_id": plan.account_id,
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

        if plan.company and any(marker in text for marker in _EVENT_DISCLOSURE_MARKERS):
            # Event-report questions (holding reports, treasury stock, capital
            # actions...) must not depend on provider tool selection, which has
            # been observed picking get_correction_lineage for them.
            required_report_patterns = _required_event_report_patterns(text)
            arguments: dict[str, object] = {
                "question": text,
                "company": plan.company,
                "top_k": 10,
            }
            if required_report_patterns and plan.period_start and plan.period_end:
                arguments.update({
                    "start_date": plan.period_start,
                    "end_date": plan.period_end,
                })
            return QuestionRoute(
                "tool",
                "event_disclosure_type_question",
                "search_disclosures",
                arguments,
                workflow=(
                    "event_disclosure_check"
                    if required_report_patterns
                    else "single"
                ),
                metric_kind="SEARCH",
                context={
                    "required_report_patterns": required_report_patterns,
                    "required_start_date": plan.period_start if required_report_patterns else None,
                    "required_end_date": plan.period_end if required_report_patterns else None,
                },
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
                    "claimed_direction": _claimed_change_direction(text),
                },
                **route_context,
            )

        if (
            plan.company
            and plan.account_status == "resolved"
            and plan.account_support_level == "derived"
            and plan.operation == "percentage_ratio"
            and len(plan.required_account_ids) == 2
        ):
            # A two-account ratio (부채비율, 영업이익률 …) has both operands in
            # the catalog formula, so it is answerable from two structured
            # facts. Leaving it unrouted sent every one of these to provider
            # tool selection, which picked a single-account tool and answered
            # none of them.
            derived = self.account_catalog.by_id.get(str(plan.account_id or ""))
            formula = dict(derived.formula or {}) if derived is not None else {}
            numerator = str(formula.get("numerator") or "")
            denominator = str(formula.get("denominator") or "")
            sources = [
                self.account_catalog.by_id.get(denominator),
                self.account_catalog.by_id.get(numerator),
            ]
            if all(item is not None and item.support_level == "structured" for item in sources):
                period = periods[0] if periods else None
                requirements = [
                    {"company": str(plan.company), "period": period, "account": item.label_ko}
                    for item in sources
                ]
                arguments = {
                    "company": str(plan.company),
                    "account": requirements[0]["account"],
                    "correction_policy": plan.correction_policy,
                    "top_k": 1,
                }
                if period:
                    arguments["start_date"] = f"{period}-01-01"
                    arguments["end_date"] = f"{period}-12-31"
                if plan.scope:
                    arguments["scope"] = plan.scope
                return QuestionRoute(
                    "tool",
                    "structured_financial_derived_ratio",
                    "get_financial_facts",
                    arguments,
                    workflow="financial_comparison",
                    metric_kind="DERIVED",
                    context={
                        "companies": [str(plan.company)],
                        "period": period,
                        "periods": [period] if period else [],
                        "metric": derived.label_ko if derived is not None else None,
                        "metric_id": str(plan.account_id or ""),
                        "intent": "financial_derived_ratio",
                        "requirements": requirements,
                        "derived_operation": "percentage_ratio",
                        "derived_operands": [denominator, numerator],
                        "required_evidence": "validated_structured_fact_per_account",
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
                context={"requested_output_unit": _requested_output_unit(text)},
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

        if (
            plan.company
            and plan.question_type == "text"
            and plan.account_status in {"unknown", "unsupported"}
            and any(marker in text for marker in _DOCUMENT_TOPIC_MARKERS)
        ):
            # Named company plus a disclosure topic but no financial account:
            # the evidence lives in report text, so search it directly instead
            # of asking the provider which tool to use.
            return QuestionRoute(
                "tool",
                "disclosure_topic_search",
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
