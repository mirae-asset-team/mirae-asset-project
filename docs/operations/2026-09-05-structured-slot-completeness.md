# Structured analysis slot completeness report

## Decision

The combined profitability and financial-health dimension requires six audited facts per financial slot: three declared accounts across at least two periods. A slot is complete only when every declared structured account reaches `min_periods`; one complete account cannot satisfy the whole slot.

The slot caps were raised from four to six for `income_trend` and `balance_sheet`. The analysis-wide cap remains 12, so the change stays within the existing bounded executor contract.

## TDD evidence

- RED 1: three accounts with two periods each were truncated to four evidence rows.
- RED 2: one account with two periods and another with one period incorrectly completed the slot.
- GREEN: both regressions pass, and the complete path preserves all six canonical accounts: `revenue`, `operating_income`, `net_income`, `total_assets`, `total_liabilities`, and `total_equity`.
- Related analysis, retrieval, claim-verification, and HCX regression: `206 passed`, `6 warnings`, `6 subtests passed`.
- Full Python regression: `946 passed`, `2 skipped`, `98 warnings`, `264 subtests passed` in `83.92s`.
- Team QA Node regression: `1 passed`, `0 failed`.

## Read-only evaluation

The legacy 120-case evaluator was rerun against the protected D-drive base, overlay, and sparse index without modifying them.

- Recall@20: `132/234 = 0.5641025641025641` (previously `114/234 = 0.48717948717948717`)
- Recall@5: `78/234 = 0.3333333333333333`
- wrong issuer/version: `0/0`
- slot completeness: `1.0`
- p50: `64.6225 ms`
- p95: `103.65321 ms`
- semantic SHA-256: `e3719d0869ad363a8bac96c88dee14421ec818bf025ddbee4f73e09f5d92bfa2`

The 7.69 percentage-point Recall@20 gain is real, but `56.41%` still fails the unchanged `95%` release gate. Residual misses remain dominated by the legacy evaluator's arbitrary exact target IDs for broad event/text questions; that contract is addressed in the next versioned evaluation task rather than by overfitting production retrieval.

## Boundaries

- D-drive base, overlay, and search index were opened read-only.
- No credential, `.env`, PEM, NCP setting, live database, or release threshold changed.
- No deployment claim is made from this task alone.
