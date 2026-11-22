# Phase 24 — Register 1 receipt connector Windows logon startup

## Scope and release status

Windows-only improvement to the existing connector, not a replacement printing system. No server deployment, migration, Railway variable, Shopify/accounting action, pairing, credential rotation, printer preference or cash-drawer change is needed. Do not install or run these scripts on the shop PC until the owner approves the Windows release and pending print queue has been reviewed. No production installer, startup, physical test or POS mutation was executed during development.

Target: Windows 11 local account **User**, existing installation `C:\PixiePinks\ReceiptConnector-190`, original device **f18b74ce-cf1f-440f-973f-579640ff46e6**, Register 1, pinned existing production origin. Register 2 **8fe7dcde-7502-40c4-897d-2a91e84f4512** is rejected by unattended binding checks. Use only **POSPrinter POS-80**, approved USB001 mapping and 80 mm roll. Barcode printer and all drawer settings remain unchanged; drawer automation stays OFF.

## Design and safety boundaries

- Task name: **PixiePinks Receipt Connector - Register 1**, root Task Scheduler folder. Trigger: only `PC\User` logon, 15-second initial delay. It uses that account's interactive token/SID with **Limited** privileges. No SYSTEM, alternate administrator, password, boot trigger or background-without-logon credentials.
- Executable: `.venv\Scripts\pythonw.exe`; arguments: `-B -m windows_print_agent run --autostart-register1`; working directory is the original installation. Pythonw does not open a console; the runtime does not depend on stdout/stderr consoles. The installer requires both existing python.exe and pythonw.exe and updated startup support. Do not substitute another interpreter or add secrets to arguments.
- Task Scheduler `IgnoreNew` plus a Windows named kernel mutex protects manual versus scheduled launches **before journal access**, including startup/network waits. The mutex key includes actual Windows SID and canonical journal directory, which also prevents installation copies sharing the journal. Process death releases kernel ownership; abandoned mutexes are accepted without deleting anything. The pre-existing physical printer mutex remains unchanged and also fences older manual connectors. Stop old binaries before copying updates.
- Ownership is held until journal close, status/log cleanup and transport close finish. Neither lock recovery nor installation removes/recreates `actions.sqlite`. Store's existing crash-to-UNKNOWN recovery, permit, encrypted transport, engine checks and acknowledgement behavior are unchanged. The runtime observes fixed lifecycle labels only.
- Recoverable DNS/network/timeouts and HTTP 408/425/429/500/502/503/504 use exponential backoff with jitter (initial 4–6 seconds, maximum 120 seconds), reset after a successful cycle. Normal connected polling remains five seconds. This retries engine communication, **never a previously journaled physical submission**. Lost permit/progress/ack responses retain existing UNKNOWN/submission fences. No generic HTTP-client mutation retry is added.
- Authentication/authorization, pin/decryption, configuration, journal and unexpected local-driver errors deliberately stop with exit 0 and a review category. Task Scheduler does not loop-restart these deliberate stops. Unexpected process/interpreter failure with nonzero exit may restart at one-minute intervals, at most three retries. A future logon is a new trigger; fix/review persistent security errors before another start. First delay is not the recovery mechanism.
- Printer offline, unavailable spooler, changed fingerprint, media UNKNOWN, busy queues and paused printing remain fail-closed in the existing engine. No media is assumed, no attestation is rewritten, and no queued work is cleared. Spooler status recovers through normal readiness polling; a driver exception during a physical action instead requires review. Runtime strict checks reject another device/register or nonreceipt policy before claims. The recorded local driver fingerprint and existing port checks preserve the approved USB mapping; verify USB001 at physical acceptance rather than reattesting automatically.
- Logs: `%LOCALAPPDATA%\PixiePinks\PrintAgent\logs\connector.jsonl`, maximum 1 MiB plus three rotated backups. Only fixed events, allowlisted exception classes/stages/numeric codes, device/job UUIDs, timestamps and finite readiness/category values are recorded. No exception text, token, pairing code, customer data, DPAPI blob, PDF or response body. Existing structured diagnostics are filtered into this file; arbitrary stderr is dropped. Logging failures cannot cause a driver retry or change job outcomes.
- `%LOCALAPPDATA%\PixiePinks\PrintAgent\runtime-status.json` is sanitized, atomic, and distinguishes RUNNING, AUTHENTICATED and printer readiness. Management status verifies process owner/PID, creation time and status freshness; stale state is not reported as current readiness. Last successful connection and last_error_category can be historical; stale data never establishes current readiness. Task Scheduler's durable **LastTaskResult** covers launch failures before Python can log. Native Scheduler/DPAPI/ACL/reboot behavior still requires Windows acceptance; fixture results do not prove those OS integrations.

## Pre-install checklist — read-only first

1. Review this PR/source and approve a Windows maintenance window. Confirm only Register 1 is targeted; no Register 2 console, identity, journal or task is touched.
2. In CRM inspect original device, version, Connected/RECEIPT, approved attestation, queue status and historical UNKNOWN/queued work. Starting the agent can dispatch **existing authorized work**; do not assume a restart is a harmless print test. Preserve all review fences; obtain separate approval for any queue recovery. Keep drawer automation OFF. Do not change daily POS flags or financial REVIEW operations.
3. Sign in to the existing local Windows **User** account. Confirm `whoami` and the configured local User SID match; management scripts enforce this. Routine task execution does not need elevation. If organization policy denies user task registration, ask an administrator to review policy while preserving this same User/Interactive/Limited principal; do not switch to SYSTEM/Highest privileges.
4. Get the reviewed source archive through the authorized GitHub browser download. Do not paste GitHub/Railway/CRM tokens into commands. Stage it outside the live install, e.g. `C:\Admin\Phase24-reviewed`. Verify the approved commit before copying. No credentials are included in the package.
5. Under separate process-control approval, let any active job finish, stop the existing manual connector with **Ctrl+C**, and verify it is stopped in CRM/Windows. For an upgrade of an already scheduled installation, use its existing management **Stop** command first, which disables future task launches; do not copy modules while a scheduled restart can occur. Do not kill Python indiscriminately. Do not copy/upgrade live modules while the process is running. Old releases do not understand the new stop-request mechanism; use their existing clean console stop.
6. Back up the **application module and management scripts only** while stopped. Preserve the existing virtual environment. Never copy DPAPI files to another user, replace credentials/attestation, or restore an old journal. If a protected-state backup is required, use the existing secured administrator procedure, including SQLite WAL consistency; do not improvise a journal migration.

## Install the reviewed Windows files

In a PowerShell window as User (replace only the reviewed source staging path):

```powershell
$Install = 'C:\PixiePinks\ReceiptConnector-190'
$Source = 'C:\Admin\Phase24-reviewed'
$Backup = 'C:\Admin\ReceiptConnector-before-phase24-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
Copy-Item -LiteralPath "$Install\windows_print_agent" -Destination $Backup -Recurse
if (Test-Path "$Install\scripts\windows") { Copy-Item -LiteralPath "$Install\scripts\windows" -Destination $Backup -Recurse }
Copy-Item -LiteralPath "$Source\windows_print_agent" -Destination $Install -Recurse -Force
New-Item -ItemType Directory -Path "$Install\scripts\windows" -Force | Out-Null
Copy-Item -Path "$Source\scripts\windows\*.ps1" -Destination "$Install\scripts\windows" -Force
Set-Location $Install
& '.\.venv\Scripts\python.exe' -B -m windows_print_agent startup-check
```

Expected sanitized `LOCAL_STARTUP_CHECK_PASSED`, startup_version 1, correct device, User, journal_present true, journal_readonly_verified true and dependencies_verified true. Dependency checks only import the installed pywin32/PDFium/Pillow/httpx/cryptography modules; they do not create a device context or printer job. This decrypts existing DPAPI records in memory to verify binding; it inspects the existing SQLite schema/state/integrity with a read-only connection and does **not** invoke Store recovery, create tables, contact CRM, pair, attest or print. Missing, empty or corrupt journals are rejected, never reinitialized. SQLite may use its normal shared-memory metadata for an existing WAL; keep the connector stopped during this check and never remove WAL/SHM files as a workaround. If invalid/missing, stop; do not initialize a new journal or rotate credentials. The dependencies are existing requirements; do not recreate the venv.

Use a fresh backup directory per upgrade; do not mix different module versions. Review downloaded `.ps1` files before removing their Mark of the Web:

```powershell
Get-ChildItem "$Install\scripts\windows\*.ps1" | Unblock-File
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\install_receipt_autostart.ps1" -WhatIf
```

This uses process-scoped RemoteSigned, not Bypass; it does not change machine policy. Stop if Group Policy requires a different approved signing/deployment process. No code-signing certificate purchase is required by this implementation.

After explicit installation approval:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\install_receipt_autostart.ps1"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\status_receipt_autostart.ps1"
```

Expected `INSTALLED_NOT_STARTED`; no process starts during installation. The enabled task will run at **next User logon** or separately approved Start. Installer checks local account, existing files, receipt-only original identity and no active connector. It rejects an unexpected same-name task or action drift instead of overwriting it. Reinstall updates the same known task; no duplicate task is created.

Application/venv/startup paths must not be reparse points or writable/owned by untrusted principals. Installer validates these ACLs and recursively checks module/venv descendants; it **does not rewrite application, credential, attestation or journal permissions**. It applies restrictive User/SYSTEM/Administrators ACLs only to the dedicated log directory. Unsafe existing ACLs require an independently reviewed administrator correction; do not weaken the check. The installer does not create another identity or modify Windows printer preferences.

## Exact management commands

All commands run as **User**, with `$Install` as above. Starts/restarts can dispatch existing authorized work and need operational approval. `-WhatIf` is available for modifying scripts.

```powershell
# Status only. A task/process existing does not establish readiness.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\status_receipt_autostart.ps1"
# Explicit start: clears only the stop-request fence and enables the same task.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\start_receipt_connector.ps1" -Action Start
# Graceful stop: disables future task starts, requests stop between actions, waits.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\start_receipt_connector.ps1" -Action Stop
# Restart only if graceful stop completes; never force-kills a driver.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\start_receipt_connector.ps1" -Action Restart
# Remove only startup task/stop fence, after connector is stopped.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Install\scripts\windows\uninstall_receipt_autostart.ps1"
```

Stop does not abort StartDoc or close a journal mid-action. It waits up to 120 seconds; if a driver hangs, it leaves the task disabled and fails for operator review. Never force a restart to recover an UNKNOWN job. A deliberate Stop leaves `stop.request` to block accidental manual relaunches; explicit Start removes it only after confirming no connector runs. Reinstall alone does not clear this fence. Uninstall removes it only after no process remains, so the original manual command works again. It retains logs and every protected file.

Do not routinely use Stop at end of day if next-logon startup is desired; shut Windows down normally after the cashier closes the register. Windows may forcibly terminate a process during shutdown; existing durable INTENT-to-UNKNOWN recovery prevents automatic replay but may require honest operator review after reboot. Register closing is not linked to process termination.

## Acceptance — separate operator approvals required

Automated tests use mocked Scheduler/Windows/transport/printer boundaries and disposable journals. **No physical acceptance has been performed for this release.**

### A. Install and identity

- Record existing User SID, device UUID, application revision, media/attestation, historical jobs and protected-file fingerprints while stopped (compare locally, never share file contents).
- Install; verify one task, User SID, Interactive/Limited, logon-only trigger, correct pythonw/arguments/working directory, IgnoreNew, 3 one-minute restart attempts and zero execution-time limit.
- Verify credentials.dpapi, attestation.dpapi and actions.sqlite/WAL have not changed during installation. No new pairing or Register 2 task/process exists.

### B. Login startup

- With reviewed queues and explicit permission to run Register 1, sign out/sign in as User. Expect one process and original CRM device connected. Status must distinguish task installed, process_count 1, expected_user_verified, runtime_fresh, authenticated and printer_ready. Confirm Hardware **Connected · RECEIPT**, exact USB001 driver mapping and cash drawer OFF.
- No console should appear. Try a separately approved simultaneous manual launch: it must exit without opening another active journal/driver loop. Do not initiate a print merely to prove locking.

### C. Reboot and restart protection

- Record existing historical jobs/acknowledgements. Reboot under approval; verify exactly one process, same identity and no historical receipt replay. Cash drawer stays closed. Never delete actions.sqlite to resolve process contention.

### D. Network, sleep and spooler recovery

- During an approved idle window, disconnect/reconnect network; inspect sanitized NETWORK/HTTP_TRANSIENT backoff and authenticated recovery without pairing. Do not change Railway configuration to simulate 502; that case is mocked in tests.
- Sleep/resume the idle PC; verify bounded retry/readiness recovery and no duplicate output. With approval, test offline printer/spooler readiness; do not alter driver preferences or issue a cashbox test. Media UNKNOWN must hold work until the existing approved media-confirmation workflow completes; never assume RECEIPT from startup.
- Security/pin/authorization/journal/local-action errors must stop for review instead of looping. Task Scheduler launch errors may have no Python log: inspect LastTaskResult and native Scheduler operational history; do not dump unexpected task actions/credentials into chat.

### E. One separately approved nonfinancial receipt

- Review historical queue fences first. Do not replay existing jobs, clear UNKNOWN or force a sale COMPLETE. Only a **new, explicitly authorized nonfinancial test** may be created via existing guarded CRM workflow.
- Observe one readable aligned receipt, drawer closed, SUBMITTED_TO_PRINTER plus one matching audit/ack, valid null-spool-ID handling. Submission is not physical verification; record observed paper honestly.
- Restart with the same journal under approval; verify zero duplicates and historical jobs unchanged. No Shopify order, payment or accounting transaction is required for this acceptance.

### F. Uninstall/rollback

- Gracefully stop; verify no active connector. Uninstall removes only this task and the stop fence. Compare protected state; it must remain intact. Do not modify printer queues, journal, credentials, attestation or CRM pairing.
- If rolling back code, restore the stopped application module backup as a complete matched version; do not mix old __main__/engine with new runtime. No server rollback/migration is needed.
- Under separate run approval, the original manual command remains:

```powershell
Set-Location 'C:\PixiePinks\ReceiptConnector-190'
& '.\.venv\Scripts\python.exe' -B -m windows_print_agent run
```

- Preserve all UNKNOWN outcomes and use existing operator review; neither rollback nor a restart is permission to replay. Do not start Register 2.

## Staff routine and troubleshooting

Morning: switch on, sign in as User, open CRM, confirm Hardware Connected · RECEIPT, then open the cashier shift and use normal daily POS. No terminal is needed. Connectivity recovery is automatic only when safe. Printer disconnected/UNKNOWN or security errors go to the supervisor; never repeat checkout to recover paper.

Evening: close/register-count through existing POS safeguards and shut down normally. Outstanding work remains durable. If automation fails, provide only the sanitized status/category, task result and relevant allowlisted JSON lines to the administrator. Never share DPAPI files, tokens, journal contents or receipt PDFs. A running process is not authenticated readiness, and submitted work is not proof of physical printing.

## Automated verification and changed files

Reviewed baseline: main `90b6dbf99e258a895fb019e62bc3b53dc96ea9f7`.

- Startup/runtime plus existing engine/GDI/diagnostic selection: **117 passed**, including **44 startup cases**. The startup cases execute the actual PowerShell management functions against mocked Scheduler/OS boundaries, parse every PowerShell entrypoint, test real process contention behind a Windows-API adapter, simulate process death and absent pythonw console streams, and reject missing/corrupt journals without initialization.
- Encrypted connector, hardware integration, daily/pilot/synthetic POS, state recovery, financial reporting and WhatsApp selection: **194 passed**.
- Disposable localhost PostgreSQL encrypted replay/queue/financial/receipt concurrency selection: **15 passed**.
- Final aggregate: **326 passed, zero failures or skips** across these selections. Existing deprecation warnings remain. Python compilation, PowerShell parser and diff checks passed. PowerShell 7.4.6 ran on Linux with isolated mocks; native Windows Task Scheduler, DPAPI under an actual logon task, ACL enforcement, reboot and physical output still require the acceptance above. No live financial or printer tests were run.

Reproduce core tests with the project Python environment:

```text
python -m pytest -q tests/test_receipt_autostart.py tests/test_print_agent.py tests/test_print_agent_diagnostics.py tests/test_receipt_connector_gdi.py tests/test_pos80_startdoc_result.py tests/test_windows_print_backend.py
```

Set `TEST_PWSH` to an installed PowerShell 7 executable to execute the management harness from pytest (otherwise that single OS-tool-dependent test is explicitly skipped). The direct management harness is `tests/windows/test_receipt_autostart.ps1`; it mocks Scheduler and never installs or prints. PostgreSQL tests require the repository's disposable localhost `TEST_POSTGRESQL_URL` fixture database; never point mutation tests at production.

Exact changed files:

- `windows_print_agent/__main__.py`: route run through the supervisor; add local nonmutating startup check.
- `windows_print_agent/runtime.py`: kernel ownership, scope/journal/dependency checks, backoff, graceful stop, rotating sanitized logging and status.
- `windows_print_agent/engine.py`: optional scope validation and best-effort lifecycle observations; existing submission/idempotency logic retained.
- `windows_print_agent/diagnostics.py`: allowlisted startup stages.
- `scripts/windows/receipt_autostart.ps1`: shared Scheduler, identity, ACL, status and management functions.
- `scripts/windows/install_receipt_autostart.ps1`
- `scripts/windows/uninstall_receipt_autostart.ps1`
- `scripts/windows/status_receipt_autostart.ps1`
- `scripts/windows/start_receipt_connector.ps1` (Start/Stop/Restart).
- `tests/test_receipt_autostart.py`
- `tests/windows/test_receipt_autostart.ps1`
- `tests/test_windows_print_backend.py`: repair stale mocked printable-area values/PIL import cache isolation for the already-existing backend; no driver implementation change.
- `docs/receipt-connector-autostart.md`

`windows.py`, `store.py`, `transport.py`, protocol, server POS/accounting/Shopify services, migrations, flags and production records are unchanged. No new dependency is needed in the existing Windows venv.
