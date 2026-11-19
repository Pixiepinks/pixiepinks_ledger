# PR #179 — finance reporting review

Reviewed head `0749ad3bd60f9b162aaa89eded83edf16b3ffc28` against main `57ab5a7` and added the fixes/acceptance tests recorded by the follow-up feature commit.

## Findings resolved

1. Both financial templates used percent-formatting, which coerces Decimal to float. For example `100999999999998.99` displayed as `100999999999998.98`. Income statement figures and the dashboard's income/expense/profit figures now use Decimal-compatible string formatting; no aggregation or monetary journal values are rewritten. The pre-existing cash balance calculation is outside this PR's income/expense aggregation change.
2. Existing accounting/Shopify regression tests exposed a sales-order page crash for a verified legacy payment whose optional `verified_at` is null. The page now preserves the recorded verifier and displays “timestamp unavailable” rather than inventing a timestamp or modifying the record.
3. Finance Overview used the server date while POS journals use Asia/Colombo dates. Its MTD period now uses Asia/Colombo, with a UTC/Sri Lanka month-boundary parity test.

Regression tests reproduced the display issues before the fixes and pass afterward. No remaining blocking code finding was identified in the reviewed reporting change.

## Financial verification

The aggregation correctly nets credits/debits and classifies the specified chart codes: 4010 and 4020 as INCOME contra-revenue, 4100 as other income, 5000 as EXPENSE COGS. All other INCOME accounts feed gross sales; other EXPENSE accounts feed other expenses. Assets, liabilities and equity do not affect profit. Date boundaries are inclusive. COGS is only posted ledger COGS, without inventory valuation inference.

Acceptance uses real POS orchestration/journal posting code with mocked Shopify and a disposable database:

| Stage | Expected LKR |
| --- | ---: |
| Sale: two units, gross sales | 17,000.00 |
| Original 10% discount | 1,700.00 |
| One-unit return, gross returns | 8,500.00 |
| Discount after the refund's 850 credit reversal | 850.00 |
| Net sales after partial refund | 7,650.00 |
| Net sales after an additional 20 discount correction | 7,670.00 |
| Posted COGS 3,500 less 500 reversal | 3,000.00 |
| Gross profit | 4,670.00 |
| Other income 50 less 10 reversal | 40.00 |
| Other expenses 70 less 4 reversal | 66.00 |
| Net profit | **4,644.00** |

For the same period, Finance Overview and Income Statement agree: total income 7,710.00, expenses 3,066.00, profit 4,644.00. Viewing reports leaves journal/line counts unchanged. A separate PostgreSQL test verifies exact fractional Decimal aggregation and exclusion of journals outside the selected dates.

## Tests

- Requested command, `python -m pytest -q tests/test_finance_reporting.py`: **4 passed**.
- Relevant finance/POS/browser/PostgreSQL/commercial-order/payment/Shopify/access regression selection: **223 passed, 2 skipped**.
- Focused financial/actual-POS/legacy-history/month-boundary/PostgreSQL acceptance selection: **9 passed**.
- Both skipped engine-specific concurrency cases were explicitly executed against separate UTF-8 PostgreSQL schemas: **1 passed each** (Draft Order retry and fulfillment preflight replacement).
- `git diff --check`: passed.

All databases were disposable/local; all upstream mutation/send paths in tests were mocked. Counts overlap and are not a full-repository test count. Existing dependency/deprecation warnings remain. Initial regressions had two legacy timestamp-rendering failures; the final run has none.

## Merge and release readiness

**Ready for code merge from this review/test perspective**, subject to the normal repository review process. The PR remains a draft; no merge or deployment was performed. No migration is required.

Before production use, the owner/accountant must verify the installed chart classifies 4010/4020 as INCOME, 4100 as other income and 5000 as EXPENSE COGS, and that POS account mappings match this convention. This review did not inspect or alter live account data. It does not authorize retrospective journal edits or POS activation.

No live journal entries, Shopify data, Railway configuration or POS checkout flags were changed. POS posting, checkout, refund and write-gate implementation files are unchanged by the follow-up fixes.
