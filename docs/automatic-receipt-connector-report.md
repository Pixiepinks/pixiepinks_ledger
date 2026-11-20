# Automatic 80 mm receipt connector implementation report

Review branch: `feature/automatic-receipt-connector`; base: main `2605abc`.
See [architecture, operational approvals, installation, acceptance, pilot and rollback](automatic-receipt-connector.md).

## Changes

- `hardware_service.py`, `hardware_routes.py`, `hardware_models.py`: reuse hardware queue,
  add receipt-only scope, default-off policy, physical synthetic-test gate, admin
  pause/resume, safe duplicate lineage, sanitized printer conditions, authenticated
  admin pin display and receipt-only encrypted RPC. Known historical test sales
  remain excluded. Existing finalized/provisional sale generators are untouched.
- `print_connector_service.py`, `windows_print_agent/envelope.py`,
  `windows_print_agent/transport.py`, `settings.py`: default-off pinned X25519 /
  HKDF / AES-GCM application tunnel, device authentication, freshness and durable
  replay fencing through untrusted Railway HTTP. Existing proxy/TLS rules unchanged.
- `alembic/versions/20261121_49_print_connector.py`: additive nonce table only;
  downgrade retains it, no financial migration or record cleanup.
- `windows_print_agent/engine.py`, `windows_print_agent/windows.py`,
  `windows_print_agent/__main__.py`, `windows_print_agent/requirements.txt`: dedicated
  POS-80 binding, single-page actual-size geometry checks, silent GDI spool dispatch,
  preserved driver/private DEVMODE, condition telemetry, secure pin/DPAPI storage,
  startup network recovery and existing durable no-replay journal.
- `windows_print_agent/install_startup.ps1`: reviewed opt-in, restricted OS identity
  Task Scheduler startup registration. No separate application login; no immediate
  start, driver change, pairing, printing or financial operation by installer.
- `static/pos_hardware.js`, `templates/pos.html`: existing CRM hardware interface for
  receipt-only enrollment, approved printer selection, pin display, physical-test
  requirement, pause/resume, status/audit and controlled duplicate receipts.
- `tests/test_receipt_connector.py`, `tests/test_receipt_connector_gdi.py`,
  `tests/test_receipt_connector_postgres.py`: encrypted API/Chromium UI, synthetic
  mocked silent printing, exact 80 mm geometry, isolated queues, replay/uncertainty,
  no added financial effects, PostgreSQL competing replay and additive migration.

## Validation

Combined regression result: **422 passed, 1 skipped**. The skip was the
PostgreSQL-only financial aggregation test, run separately as recorded below.
An additional encrypted streaming-limit test passed; after the final transport
change the connector/GDI suite passed **24 tests**, and the final UI/reprint
idempotency checks passed separately. Test services,
printing, Shopify and financial fixtures are mocked/disposable; no production
runtime, Windows printer, Railway secret or live Shopify connection was used.

PostgreSQL command (local disposable schemas):

```sh
TEST_POSTGRESQL_URL=postgresql+psycopg2://agent@127.0.0.1:55433/pixiepinks_test \
DATABASE_URL=sqlite:////tmp/connector-pg-base.sqlite python -m pytest -xq --disable-warnings \
  tests/test_receipt_connector_postgres.py tests/test_hardware_postgres.py \
  tests/test_pos_postgres.py tests/test_pos_provisional_postgres.py tests/test_retail_postgres.py
```

Result: **12 passed**, including migration replay and concurrent workers/claims.

The combined run covered POS/adapter/browser, provisional/finalized receipts,
branding/geometry, finance, inventory/adapter, retail labels, WhatsApp quick
messages/sales orders, legacy hardware/agent/proxy/integration and new connector
security/GDI/browser tests. No failed test. Existing deprecation warnings remain.

Additional PostgreSQL accounting check:

```sh
TEST_POSTGRESQL_URL=postgresql+psycopg2://agent@127.0.0.1:55433/pixiepinks_test \
DATABASE_URL=sqlite:////tmp/connector-finance-pg.sqlite python -m pytest -q \
  tests/test_finance_reporting_pos.py::test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates
```

Result: **1 passed**. The combined run’s sole skipped case is therefore verified
on PostgreSQL, with no unresolved automated-test skip. Including the additional
streaming-limit test, 436 distinct test cases passed across these runs; the final
24-case transport and 2-case reprint/UI reruns also passed.
JavaScript syntax, Python compilation and `git diff --check` passed.

## Remaining release blockers and limits

- Requires human security review/approval of the new pinned application protocol,
  secret provisioning and rotation process; no proxy origin is fabricated.
- Requires approved code deployment/migration and explicit default-off gate changes.
  Neither deployment nor settings changes were performed.
- Windows vendor driver/spooler behavior, DPAPI under the startup identity, USB
  reconnect, cutter, physical margins and unattended boot require the documented
  physical acceptance. Mocked GDI tests do not certify hardware or paper output.
- The two old POS test sales were not modified/probed/reconciled/retried. A same-shop
  REVIEW operation may still block new completion and therefore the real-sale pilot.
  Printing never bypasses it. No live completeness/read-back evidence is claimed.
- Spool IDs are not proof of physical output; uncertain delivery never auto-retries.
  Windows/driver queue resume can still produce output after reconnect. Inspect it
  before approving any duplicate. No exactly-once physical printing claim is made.
- No automatic provisional receipt, drawer, raw USB, label routing, financial,
  Shopify, inventory, import, Draft Order or webhook change is introduced.
