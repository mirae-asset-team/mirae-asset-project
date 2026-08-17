"""Decimal-only arithmetic allowlist for grounded answers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Iterable

from .agent_contracts import CalculationResult


ALLOWED_OPERATIONS = frozenset({"lookup", "difference", "ratio", "growth_rate", "sum"})


def _decimals(values: Iterable[object]) -> list[Decimal]:
    result: list[Decimal] = []
    for value in values:
        try:
            decimal = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"not a Decimal value: {value!r}") from exc
        if not decimal.is_finite():
            raise ValueError("operands must be finite")
        result.append(decimal)
    return result


def calculate(
    operation: str,
    operands: Iterable[object],
    *,
    unit: str | None = None,
    evidence_ids: Iterable[str] = (),
) -> CalculationResult:
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(f"operation not allowed: {operation}")
    values = _decimals(operands)
    if operation == "lookup":
        if len(values) != 1:
            raise ValueError("lookup needs one operand")
        value = values[0]
    elif operation == "sum":
        if not values:
            raise ValueError("sum needs at least one operand")
        value = sum(values, Decimal(0))
    elif operation == "difference":
        if len(values) != 2:
            raise ValueError("difference needs two operands")
        value = values[1] - values[0]
    elif operation in {"ratio", "growth_rate"}:
        if len(values) != 2 or values[0] == 0:
            raise ValueError(f"{operation} needs two operands and a non-zero baseline")
        value = ((values[1] - values[0]) / values[0] * Decimal(100)) if operation == "growth_rate" else values[1] / values[0]
    else:  # pragma: no cover - protected by allowlist
        raise ValueError(operation)
    return CalculationResult(operation=operation, value=value, unit=unit, evidence_ids=list(evidence_ids), operands=values)
