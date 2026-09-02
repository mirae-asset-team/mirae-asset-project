"""Central financial-account catalog and deterministic account resolution."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping


_DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "financial_account_catalog.json"
)
_SUPPORT_LEVELS = frozenset(
    {"structured", "retrieval_only", "derived", "ambiguous", "unsupported"}
)
_RETRIEVAL_ROUTES = frozenset(
    {"financial_fact", "evidence_search", "calculator", "clarification", "insufficient_evidence"}
)
_STATEMENT_TYPES = frozenset(
    {
        "income_statement",
        "balance_sheet",
        "cash_flow_statement",
        "changes_in_equity",
        "per_share_information",
        "derived_metric",
    }
)
_MATCH_PRIORITY = {"exact_label": 0, "exact_alias": 1, "typo_alias": 2, "ambiguous_expression": 3}
_ACCOUNT_SUFFIXES = (
    "은", "는", "이", "가", "을", "를", "의", "과", "와", "도", "만", "에", "로", "으로",
    "에서", "부터", "까지", "보다", "대비", "이며", "이고", "인지", "인가", "에는", "에대해",
    "에대한", "관련", "기준", "금액", "값", "규모", "기여", "영향", "전망", "표", "셀", "항목",
    "계정", "얼마", "수치", "실적", "결과", "정보", "내역", "추이", "변화", "증가", "감소", "비교",
    "차이", "증감", "비율", "비중", "합계", "총액", "최근", "상위", "하위", "넘", "초과",
    "이상", "알려", "찾아", "조회", "확인", "주",
    # Colloquial tails: a register change must not hide the account word.
    "좀", "궁금", "어때", "어떻", "말해", "보여", "몇",
)


def normalize_account_text(value: str) -> str:
    """Normalize Unicode, whitespace, and symbols without fuzzy matching."""

    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        if value == []:
            return ()
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class FinancialAccount:
    canonical_id: str
    label_ko: str
    label_en: str
    statement_type: str
    period_type: str
    expected_value_type: str
    aliases: tuple[str, ...]
    typo_aliases: tuple[str, ...]
    xbrl_concepts: tuple[str, ...]
    support_level: str
    retrieval_route: str
    ambiguous_with: tuple[str, ...]
    required_accounts: tuple[str, ...]
    formula: Mapping[str, object] | None
    description: str
    cautions: tuple[str, ...]

    @classmethod
    def from_mapping(cls, item: Mapping[str, object]) -> "FinancialAccount":
        required = (
            "canonical_id", "label_ko", "label_en", "statement_type", "period_type",
            "expected_value_type", "support_level", "retrieval_route", "description",
        )
        if any(not isinstance(item.get(key), str) or not item.get(key) for key in required):
            raise ValueError("financial account entries require non-empty scalar identity fields")
        support_level = str(item["support_level"])
        retrieval_route = str(item["retrieval_route"])
        statement_type = str(item["statement_type"])
        if support_level not in _SUPPORT_LEVELS:
            raise ValueError(f"invalid support level: {support_level}")
        if retrieval_route not in _RETRIEVAL_ROUTES:
            raise ValueError(f"invalid retrieval route: {retrieval_route}")
        if statement_type not in _STATEMENT_TYPES:
            raise ValueError(f"invalid statement type: {statement_type}")
        formula = item.get("formula")
        if formula is not None and not isinstance(formula, Mapping):
            raise ValueError("formula must be an object or null")
        return cls(
            canonical_id=str(item["canonical_id"]),
            label_ko=str(item["label_ko"]),
            label_en=str(item["label_en"]),
            statement_type=statement_type,
            period_type=str(item["period_type"]),
            expected_value_type=str(item["expected_value_type"]),
            aliases=_strings(item.get("aliases", []), "aliases"),
            typo_aliases=_strings(item.get("typo_aliases", []), "typo_aliases"),
            xbrl_concepts=_strings(item.get("xbrl_concepts", []), "xbrl_concepts"),
            support_level=support_level,
            retrieval_route=retrieval_route,
            ambiguous_with=_strings(item.get("ambiguous_with", []), "ambiguous_with"),
            required_accounts=_strings(item.get("required_accounts", []), "required_accounts"),
            formula=dict(formula) if formula is not None else None,
            description=str(item["description"]),
            cautions=_strings(item.get("cautions", []), "cautions"),
        )


@dataclass(frozen=True, slots=True)
class AmbiguousExpression:
    expression: str
    candidates: tuple[str, ...]
    warning: str


@dataclass(frozen=True, slots=True)
class FinancialAccountResolution:
    status: str
    canonical_id: str | None
    label_ko: str | None
    match_type: str | None
    support_level: str
    retrieval_route: str
    candidates: tuple[str, ...] = ()
    warning: str | None = None
    matched_text: str | None = None
    statement_type: str | None = None
    required_accounts: tuple[str, ...] = ()
    formula: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Match:
    start: int
    end: int
    match_type: str
    surface: str
    account_id: str | None = None
    candidates: tuple[str, ...] = ()
    warning: str | None = None

    @property
    def length(self) -> int:
        return self.end - self.start


class FinancialAccountCatalog:
    """Validated catalog with deterministic, longest-surface resolution."""

    def __init__(
        self,
        accounts: Iterable[FinancialAccount],
        ambiguous_expressions: Iterable[AmbiguousExpression],
        legacy_ids: Mapping[str, str],
    ) -> None:
        ordered = tuple(accounts)
        self.accounts = ordered
        self.by_id = {account.canonical_id: account for account in ordered}
        if len(self.by_id) != len(ordered):
            raise ValueError("financial account canonical IDs must be unique")
        self.ambiguous_expressions = tuple(ambiguous_expressions)
        self.legacy_ids = dict(legacy_ids)
        known = set(self.by_id)
        for account in ordered:
            references = (*account.ambiguous_with, *account.required_accounts)
            if any(reference not in known for reference in references):
                raise ValueError(f"unknown account reference in {account.canonical_id}")
        for item in self.ambiguous_expressions:
            if any(candidate not in known for candidate in item.candidates):
                raise ValueError(f"unknown ambiguity candidate for {item.expression}")
        if any(target not in known for target in self.legacy_ids.values()):
            raise ValueError("legacy IDs must point to known accounts")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "FinancialAccountCatalog":
        if payload.get("schema_version") != "financial-account-catalog-v1":
            raise ValueError("unsupported financial account catalog schema")
        raw_accounts = payload.get("accounts")
        raw_ambiguities = payload.get("ambiguous_expressions", [])
        legacy_ids = payload.get("legacy_ids", {})
        if not isinstance(raw_accounts, list) or not all(isinstance(item, Mapping) for item in raw_accounts):
            raise ValueError("financial account catalog requires an accounts list")
        if not isinstance(raw_ambiguities, list) or not all(isinstance(item, Mapping) for item in raw_ambiguities):
            raise ValueError("ambiguous_expressions must be a list")
        if not isinstance(legacy_ids, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in legacy_ids.items()
        ):
            raise ValueError("legacy_ids must be a string mapping")
        ambiguities: list[AmbiguousExpression] = []
        for item in raw_ambiguities:
            expression = item.get("expression")
            warning = item.get("warning")
            if not isinstance(expression, str) or not expression or not isinstance(warning, str) or not warning:
                raise ValueError("ambiguous expressions require expression and warning")
            ambiguities.append(
                AmbiguousExpression(
                    expression=expression,
                    candidates=_strings(item.get("candidates", []), "candidates"),
                    warning=warning,
                )
            )
        return cls(
            (FinancialAccount.from_mapping(item) for item in raw_accounts),
            ambiguities,
            {str(key): str(value) for key, value in legacy_ids.items()},
        )

    @staticmethod
    def _occurrences(text: str, surface: str) -> Iterable[tuple[int, int]]:
        offset = 0
        while surface and (found := text.find(surface, offset)) >= 0:
            end = found + len(surface)
            tail = text[end:]
            if not tail or tail[0].isdigit() or tail.startswith(_ACCOUNT_SUFFIXES):
                yield found, end
            offset = found + 1

    def _matches(self, text: str) -> list[_Match]:
        result: list[_Match] = []
        seen: set[tuple[int, int, str, str | None]] = set()
        for account in self.accounts:
            surfaces = (
                ("exact_label", account.label_ko),
                *(("exact_alias", alias) for alias in account.aliases),
                *(("typo_alias", alias) for alias in account.typo_aliases),
            )
            for match_type, raw_surface in surfaces:
                surface = normalize_account_text(raw_surface)
                for start, end in self._occurrences(text, surface):
                    key = (start, end, match_type, account.canonical_id)
                    if key not in seen:
                        result.append(_Match(start, end, match_type, raw_surface, account.canonical_id))
                        seen.add(key)
        for item in self.ambiguous_expressions:
            surface = normalize_account_text(item.expression)
            for start, end in self._occurrences(text, surface):
                result.append(
                    _Match(
                        start,
                        end,
                        "ambiguous_expression",
                        item.expression,
                        candidates=item.candidates,
                        warning=item.warning,
                    )
                )
        return result

    @staticmethod
    def _non_shadowed(matches: Iterable[_Match]) -> list[_Match]:
        rows = list(matches)
        result: list[_Match] = []
        for match in rows:
            shadowed = any(
                other is not match
                and other.start <= match.start
                and match.end <= other.end
                and (
                    other.length > match.length
                    or (
                        other.start == match.start
                        and other.end == match.end
                        and _MATCH_PRIORITY[other.match_type] < _MATCH_PRIORITY[match.match_type]
                    )
                )
                for other in rows
            )
            if not shadowed:
                result.append(match)
        return result

    def resolve(self, value: str) -> FinancialAccountResolution:
        raw = unicodedata.normalize("NFKC", str(value)).strip()
        if not raw:
            return self._unknown()
        folded = raw.casefold()
        for account in self.accounts:
            if any(folded == unicodedata.normalize("NFKC", concept).strip().casefold() for concept in account.xbrl_concepts):
                return self._resolved(account, "exact_xbrl_concept", raw)

        compact = normalize_account_text(raw)
        matches = self._non_shadowed(self._matches(compact))
        if not matches:
            legacy_target = self.legacy_ids.get(raw)
            if legacy_target is not None:
                return self._resolved(self.by_id[legacy_target], "legacy_id", raw)
            return self._unknown()

        ambiguity_matches = [match for match in matches if match.match_type == "ambiguous_expression"]
        account_matches = [match for match in matches if match.account_id is not None]
        distinct_accounts = {str(match.account_id) for match in account_matches}
        if ambiguity_matches or len(distinct_accounts) > 1:
            candidates = list(
                dict.fromkeys(
                    candidate
                    for match in matches
                    for candidate in (match.candidates or ((str(match.account_id),) if match.account_id else ()))
                )
            )
            selected = max(matches, key=lambda item: (item.length, -_MATCH_PRIORITY[item.match_type]))
            warning = next(
                (match.warning for match in ambiguity_matches if match.warning),
                "여러 재무계정 표현이 함께 발견되어 하나의 계정으로 임의 선택하지 않습니다.",
            )
            return FinancialAccountResolution(
                status="ambiguous",
                canonical_id=None,
                label_ko=None,
                match_type=selected.match_type,
                support_level="ambiguous",
                retrieval_route="clarification",
                candidates=tuple(candidates),
                warning=warning,
                matched_text=selected.surface,
            )

        selected = min(
            account_matches,
            key=lambda item: (_MATCH_PRIORITY[item.match_type], -item.length, item.start),
        )
        return self._resolved(self.by_id[str(selected.account_id)], selected.match_type, selected.surface)

    @staticmethod
    def _unknown() -> FinancialAccountResolution:
        return FinancialAccountResolution(
            status="unknown",
            canonical_id=None,
            label_ko=None,
            match_type=None,
            support_level="unsupported",
            retrieval_route="insufficient_evidence",
            warning="등록되지 않은 재무계정 표현이며 유사 계정으로 자동 매핑하지 않습니다.",
        )

    @staticmethod
    def _resolved(
        account: FinancialAccount,
        match_type: str,
        matched_text: str,
    ) -> FinancialAccountResolution:
        status = "resolved"
        candidates: tuple[str, ...] = ()
        warning: str | None = None
        if account.support_level == "ambiguous":
            status = "ambiguous"
            candidates = account.ambiguous_with
            warning = f"{account.label_ko}의 범위 또는 세부 계정을 확인해야 합니다."
        elif account.support_level == "unsupported":
            status = "unsupported"
            warning = f"{account.label_ko}은 현재 검증 데이터와 로직으로 지원하지 않습니다."
        return FinancialAccountResolution(
            status=status,
            canonical_id=account.canonical_id,
            label_ko=account.label_ko,
            match_type=match_type,
            support_level=account.support_level,
            retrieval_route=account.retrieval_route,
            candidates=candidates,
            warning=warning,
            matched_text=matched_text,
            statement_type=account.statement_type,
            required_accounts=account.required_accounts,
            formula=dict(account.formula) if account.formula is not None else None,
        )

    def alias_expansions(self) -> dict[str, list[str]]:
        expansions: dict[str, list[str]] = {}
        for account in self.accounts:
            surfaces = list(dict.fromkeys((account.label_ko, *account.aliases, *account.typo_aliases)))
            for key in (account.canonical_id, *surfaces):
                expansions[key] = list(surfaces)
        return expansions

    def structured_extraction_aliases(self) -> dict[str, frozenset[str]]:
        return {
            account.canonical_id: frozenset(
                normalize_account_text(surface) for surface in (account.label_ko, *account.aliases)
            )
            for account in self.accounts
            if account.support_level == "structured"
        }

    def structured_account_ids(self) -> tuple[str, ...]:
        return tuple(account.canonical_id for account in self.accounts if account.support_level == "structured")


def _catalog_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("DISCLOSURE_CONFIG_DIR")
    if configured:
        candidate = Path(configured) / "financial_account_catalog.json"
        if candidate.exists():
            return candidate
    return _DEFAULT_CATALOG_PATH


@lru_cache(maxsize=8)
def _load_catalog_cached(path: str) -> FinancialAccountCatalog:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("financial account catalog root must be an object")
    return FinancialAccountCatalog.from_mapping(payload)


def load_financial_account_catalog(path: str | Path | None = None) -> FinancialAccountCatalog:
    return _load_catalog_cached(str(_catalog_path(path).resolve()))


def clear_financial_account_catalog_cache() -> None:
    _load_catalog_cached.cache_clear()


def resolve_financial_account(
    value: str,
    *,
    catalog: FinancialAccountCatalog | None = None,
) -> FinancialAccountResolution:
    return (catalog or load_financial_account_catalog()).resolve(value)
