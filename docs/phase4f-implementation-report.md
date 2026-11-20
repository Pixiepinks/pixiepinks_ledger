# Phase 4F implementation report

Branch: `feature/retail-phase4f-hardware-printing`, based on main `0620675` (merged finance PR #179). Final commit/draft PR and test totals are in the delivery record.

## Implementation

- Secure register/location-bound device enrollment, five-minute single-use pairing, server-side token hashes, activation/deactivation, rotation/revocation and last-seen telemetry.
- Immutable downstream hardware jobs with per-source business keys, one dispatch permit, append-only audit, bounded authenticated PDF artifacts, expiry and explicit physical confirmation.
- Automatic original receipts for newly verified COMPLETE sales, positive-cash-only drawer actions, opt-in cash movement actions, protected manual openings, duplicate receipts without drawer, and existing approved retail label PDFs exclusively to their label queue.
- One shared DBL resource: two normal queues, operator-confirmed media, global Windows mutex, empty/ready checks, shop-wide in-flight/uncertainty coordination and fixed isolated drawer queue selected by the owner.
- Outbound-only Windows agent, DPAPI credentials/driver attestation, permanent local SQLite action journal, TLS origin/redirect restrictions, raster/GDI printing, vendor/private driver fingerprint checks and no raw USB/ESC-POS or remote command execution.
- Compact POS hardware status/admin/history/recovery UI; barcode/cart logic retained; existing PDF/browser fallbacks retained.
- Separate account/ACL/signature/hash-checked Windows startup instructions, source build scripts and physical/offline/USB/spooler/retirement guides.

The complete runtime source is in `windows_print_agent/`; server implementation is `hardware_models.py`, `hardware_service.py`, `hardware_routes.py`. Migration **20261120_48** follows **20261119_47**. Existing POS receipt generation is reused with optional transactional/duplicate behavior; financial order creation, payment verification, tax/prices and inventory mutation code are not changed. POS service change is only registration of the granular manual drawer permission. No server-side Shopify token is sent to the agent.

## Verification scope

Automated tests cover two normal queues on one physical resource, receipt/label routing and exact label raster pixels, per-user/private driver settings, manual media confirmation, cash/card/bank/split sale rules, completed/unpaid/uncertain states, duplicate receipts, Cashbox job separation, drawer timeouts/age limits, offline/USB/spooler failures, duplicate delivery, process restart, lost ACK, source eligibility, credential revocation/rotation/pair expiry, cross-register access, bounded PDF streaming, server/local audit, concurrent workers and PostgreSQL immutability. A real authenticated HTTP protocol is exercised into TestClient with a fake Windows backend; browser tests exercise hardware controls and unchanged repeated scanning.

Windows APIs and device actions are mocked; physical output is not certified by those results. Maintainer API evidence (DEVMODE DriverData and CreateDC signature) is in [the source evidence manifest](evidence/phase4f-windows-api.json). There is no raw device command guessed from the drawer manual's electrical notes.

## Release requirements and blockers

- No tested/signed Windows executable or installer was built on Linux. The supplied Windows build script creates an unsigned onedir bundle for review/test/signing on appropriate infrastructure. Do not claim a package was produced or weaken Windows protections.
- Actual DBL driver/paper/cut/USB and isolated Cashbox behavior must be verified under the **dedicated agent account**, including effective preferences, queue ACL and unattended startup. The small control slip and its extra cut/feed need operator acceptance.
- Physical printing/cutting/drawer position has no verified device sensor. Windows spool acceptance is explicitly SUBMITTED, not PRINTED. Staff inspection supplies CONFIRMED. No automatic media detection is claimed.
- External Windows apps can bypass this agent's lock. Stop/review them when changing media or resolving uncertainty. Revocation cannot undo bytes already submitted. Partial batches require explicit remaining-copy recovery.
- Hardware deployment/activation is separate owner approval; all three new gates default false. Existing Shopify/POS checkout/refund/product/inventory settings are untouched. No financial pilot bypass or live test transaction was added.

Apply the additive migration only after approved backup/release. Keep job/audit tables and local action journal on rollback; do not reset attempts or replay an uncertain drawer command. See [architecture and release guide](retail-phase4f-hardware.md), [Windows installation/recovery](windows-print-agent-install.md) and [physical acceptance checklist](phase4f-hardware-acceptance.md).

No live Shopify orders/refunds/stock changes, journal edits, Railway variable changes, physical drawer commands, merge or deployment occurred during development.

## Isolated drawer transport prerequisite

The administrator must set the isolated drawer queue to **Print directly to the printer**, turn **Keep printed documents** off, and obtain manufacturer-supported evidence and authorized physical interruption tests showing that USB/spooler recovery does not replay the driver cashbox action. The agent checks the direct/no-retention queue attributes and requires a separate local no-replay attestation. Unknown or unsupported behavior leaves drawer automation blocked; receipts and labels can be accepted independently. The application journal prevents application retries, but cannot establish exactly-once physical behavior inside a Windows driver or printer firmware. No physical interruption test or drawer action was performed during development.

## Automated execution record

All databases and Shopify/Windows interfaces below are isolated development fixtures. No physical printer was used.

| Run | Result |
| --- | --- |
| Broad hardware/POS/accounting, Retail/scanner, inventory, Shopify Draft Orders/payments/fulfillment, staff and WhatsApp regression | 547 passed, 2 PostgreSQL-engine-only skips, 0 failed |
| Final hardware/server/agent/Windows API, PostgreSQL concurrency, browser POS and finance focused suite | 91 passed, 0 failed |
| PostgreSQL Draft Order concurrent retry claim, separate fresh schema | 1 passed |
| PostgreSQL fulfillment concurrent expired-preflight replacement, separate fresh schema | 1 passed |
| Compact 30x15 SKU/price PDF dimensions, content and barcode decoding regression | 38 passed |
| JavaScript syntax checks (POS, hardware, Retail) and Python compile checks | Passed |

Both broad-run skips were executed successfully on PostgreSQL in the separate runs above. Runs overlap; their counts must not be summed as unique tests. Existing deprecation warnings are reported by pytest. Windows executable packaging, PowerShell startup execution and actual driver/USB/print/cut/drawer behavior remain unexecuted hardware acceptance requirements.
