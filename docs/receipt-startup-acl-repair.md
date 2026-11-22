# Phase 24A — reviewed Windows startup ACL repair

## Assessment and boundaries

The Phase 24 refusal is correct: inherited **Authenticated Users Modify** lets other authenticated principals change executable code or replace startup paths. Membership of Administrators does not make an ordinary User PowerShell token elevated. Do not bypass `Assert-ReceiptAcl`, use SYSTEM, grant Everyone FullControl, reset a tree with `icacls /reset /T`, take ownership wholesale, re-pair, or recreate the journal.

This procedure targets only **DESKTOP-ADTR67Q\User**, device **f18b74ce-cf1f-440f-973f-579640ff46e6**, `C:\PixiePinks\ReceiptConnector-190` and, only when necessary and isolated, its `C:\PixiePinks` ancestor. It does not create, enable or start a task. It does not stop any process, alter printer preferences, touch Shopify/accounting/CRM jobs, change device identity, or modify DPAPI/attestation/SQLite records or their ACLs.

**Do not execute Apply yet.** First obtain and review a fresh private dry-run report. No actual shop-PC ACL inventory or remediation has been executed during development.

## Scope and inheritance strategy

- Enumerate the **whole connector installation**, including Python/pythonw, `.venv` and `pyvenv.cfg`, installed libraries, modules, `scripts\windows` and parents. Read-only inventory also follows the configured base Python home/stdlib tree, relevant ancestors, repair-tool source/staging chain, and original protected-state files/ancestors. Reject junctions/reparse points, hard-linked files inside the repair scope (their ACL is shared with possible outside links), missing startup files, untrusted owners, unsupported ACEs, deny rules requiring semantic review, Python path overrides and system-site-package configuration requiring additional review.
- Confirm the original device by decrypting only the two existing DPAPI records in memory under User. A small .NET DTO reader returns a boolean; token-bearing JSON never goes through PowerShell `ConvertFrom-Json`, output, cmdlet parameters or the report. It never executes the broad-writable application Python during repair. Only encrypted-file hashes and ACL metadata enter the inventory.
- Report **every nontrusted write-capable allow ACE**, including inherited ACEs, with SID, flags, before/after masks and path. Standard broad Modify becomes Read/Execute/Synchronize. Existing read/execute grants are retained; write-only grants may disappear. Preserve owner/group/SACL; change DACLs only. Trusted access is granted explicitly to **the exact User SID, SYSTEM, Administrators** on changed objects, including protected files that might previously have relied on broad group access.
- Protect the connector root by copying inherited entries to explicit entries, then remove only nontrusted write/delete/delete-child/DACL-change/ownership-change bits. Descendants keep their existing inheritance/protection setting; only actual explicit unsafe grants are edited. Parent inheritance refresh is re-read and validated. No blanket child ACL reset or recursive ownership change is used.
- Change `C:\PixiePinks` **only if its ACL needs repair and it has no other child besides this connector**. If another application, Register 2 directory, file or hidden child exists, the report blocks that parent change: Windows inheritance propagation could otherwise affect unrelated objects. There is no force override. A safe shared parent is left untouched, including during rollback. A blocked shared-parent case needs a separately reviewed isolation/placement design; do not move/repoint the existing connector as a workaround.
- External paths (base interpreter, protected state, tool source, user-profile/volume ancestors) are **audit-only**. Unsafe effective write access there blocks repair instead of modifying unrelated software or Windows. A create-only right on an already-existing external ancestor cannot replace the existing protected child; it is reported but does not cause the tool to rewrite a volume root. Inherit-only external ACEs are evaluated against the actual audited descendants. The existing startup check remains strict for its watched paths.
- The Phase 24 checker is strengthened to include launcher script directories/files and delete-child rights. No trust principal or unsafe-path exemption is added to that checker.

An ACL audit is not a malware/code-integrity attestation. The installation has been broadly writable: independently compare application files to the approved release, and verify interpreter/venv provenance before later allowing it to execute. The tool records content hashes and rejects changes during the approved window but cannot prove pre-existing files are authentic.

## Maintenance and approval prerequisites

1. Owner reviews the PR and source. Keep Register 1 stopped; no task may be enabled/running. If a task or process exists, stop/disable it only through the separately approved Phase 24 procedure. This tool only reports/refuses that state. Register 2 remains untouched.
2. Establish an exclusive ACL/code maintenance window: no other logged-in user, deployment, pip update, file copy, installer, ACL editor or connector may change these paths. Check for competing writers without killing them automatically. Windows ACL updates are **not atomic across a tree**; the plan hash is not a compare-and-swap guarantee. Fresh inventory and per-object checks detect drift, not every possible race.
3. Keep an independent, genuine administrator approval record identifying computer, User SID, Register 1, reviewed plan hash, scope, preservation of read/execute, and acceptance of the controlled non-atomic procedure. The approval reference is an audit identifier, not a password or security token. Do not invent approval on the tool's behalf.
4. Approvals expire after **15 minutes**. Review again after any change, blocker or expiry. Normal/elevated plans can match because the actual elevation flag is not part of the ACL inventory hash; Apply checks elevation independently and requires the same SID.
5. Use a **reviewed private staging directory outside `C:\PixiePinks`**. Do not run an elevated repair script from the currently broad-writable connector directory. The tool checks its own source/parent ownership and write permissions before reading protected state.

## Stage the two reviewed files (ordinary User PowerShell)

Use Windows PowerShell 5.1 (`powershell.exe`) on the shop PC. PowerShell 7.4 is used for mocked development tests; native Windows/DPAPI/inheritance acceptance remains required.

Download the reviewed source through the authorized GitHub browser session. Copy **only** `receipt_acl_repair.ps1` and `repair_receipt_startup_acl.ps1` into:

```powershell
$Stage = Join-Path $env:LOCALAPPDATA 'PixiePinksAclReview'
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
# Copy the two reviewed files from the downloaded source into $Stage.
Get-FileHash -Algorithm SHA256 "$Stage\receipt_acl_repair.ps1", "$Stage\repair_receipt_startup_acl.ps1"
```

Compare those hashes to the reviewed PR files before elevation. Do not use a token in any command. Do not copy credential files. After source review, remove Mark of the Web from only these files if needed:

```powershell
Unblock-File -LiteralPath "$Stage\receipt_acl_repair.ps1"
Unblock-File -LiteralPath "$Stage\repair_receipt_startup_acl.ps1"
```

Commands below use process-scoped `RemoteSigned`, not Bypass, and do not change machine execution policy. If Group Policy blocks local reviewed scripts or Add-Type, stop for the administrator's approved deployment mechanism; do not weaken policy.

## Read-only dry run — default execution mode

```powershell
$Stage = Join-Path $env:LOCALAPPDATA 'PixiePinksAclReview'
$PlanFile = Join-Path $Stage 'acl-plan.json'
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Stage\repair_receipt_startup_acl.ps1" -Mode Plan |
    Set-Content -LiteralPath $PlanFile -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw 'Preflight stopped. Inspect the sanitized report; make no ACL change.' }
$P = Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
$P | Select-Object mode, write_authorized, current_token_elevated, approval_hash
$P.body.changes | Format-Table path, operation, preserve_read_execute -AutoSize
$P.body.issues | Format-Table path, sid, inherited, before_mask, after_mask, repairable -AutoSize
$P.body.blockers
```

Expected: `READ_ONLY_PLAN`, `write_authorized=false`; `current_token_elevated=false` is normal in the current unelevated session. No task/ACL/application/journal changes occur. The report file is created only by the explicit redirection above; keep it private, not in chat. Report includes full before-SDDL, owners, flags and content hashes; it contains no token, DPAPI plaintext, customer receipt or database rows.

Review especially **S-1-5-11** (Authenticated Users), **S-1-5-32-545** (Users), **S-1-1-0** (Everyone) and any other foreign SID. Check that no genuine unrelated app or Register 2 is inside the approved tree. Do not approve a plan with blockers.

Stop conditions include `SHARED_PARENT_PROPAGATION_REQUIRES_SEPARATE_REVIEW`, unsafe external interpreter/state/tool paths, owner/deny/conditional ACE uncertainty, reparse/hard links, a different device/user/computer, running connector, enabled task, unavailable dependencies, or changed path membership. A successful prior startup-check is not permanent evidence.

## Administrator-approved application — separate action

Only after reviewing the dry run and recording actual approval, open an elevated shell **as the same User**, not another administrator:

```powershell
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile'
```

Accept UAC only for `DESKTOP-ADTR67Q\User`. Cancel if credentials for another account are requested. In the new shell:

```powershell
whoami
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
```

Expect the same User and `True`. Set variables again; do not reuse an expired approval:

```powershell
$Stage = Join-Path $env:LOCALAPPDATA 'PixiePinksAclReview'
$PlanFile = Join-Path $Stage 'acl-plan.json'
$P = Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
$ApprovedHash = Read-Host 'Paste the exact separately reviewed 64-character approval_hash'
$ApprovalRef = Read-Host 'Enter the genuine administrator approval record reference'
$BackupParent = Join-Path $env:LOCALAPPDATA 'PixiePinksAclBackups'
New-Item -ItemType Directory -Path $BackupParent -Force | Out-Null
$Backup = Join-Path $BackupParent ('repair-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
# Optional additional confirmation check; WhatIf performs no ACL modification/export.
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Stage\repair_receipt_startup_acl.ps1" `
    -Mode Apply -PlanFile $PlanFile -ApprovalHash $ApprovedHash -ApprovalReference $ApprovalRef `
    -ConfirmTarget 'DESKTOP-ADTR67Q\User|REGISTER1|ACL ONLY' -BackupDirectory $Backup -WhatIf
```

The exact hash, reference and confirmation are not inferred from `$P` automatically: the owner enters the reviewed approval. For the approved execution, repeat the same command **without `-WhatIf`**:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Stage\repair_receipt_startup_acl.ps1" `
    -Mode Apply -PlanFile $PlanFile -ApprovalHash $ApprovedHash -ApprovalReference $ApprovalRef `
    -ConfirmTarget 'DESKTOP-ADTR67Q\User|REGISTER1|ACL ONLY' -BackupDirectory $Backup
```

Apply recomputes the complete inventory, checks its digest and rechecks approval expiry after scanning and before the first selected ACL write. It creates a new private backup directory (User/SYSTEM/Administrators only), exports original SDDL/owner/group/protection flags and the approval record to `before-acls.json`, then changes only approved code paths. Existing backup directories/files are never overwritten. No original owner/group/SACL is changed; no `/reset`, takeown, task action or protected-state ACL write occurs.

Success must be **`ACL_REPAIR_VALIDATED_NO_TASK_STARTED`** with `backup_file` and `backup_sha256`. Retain both. The entire trust chain is re-read: no broad code writes, trusted access retained, no new paths/siblings, file hashes/owners unchanged, and all audit-only ACLs unchanged. `after-inventory.json` provides the result. No automatic installer/task start follows.

If any step fails, **some ACLs may already be changed**. Preserve the backup; do not claim rollback or success. The tool deliberately stops rather than hiding partial results or automatically restoring unsafe permissions. The error report's `acl_mutation_requested` distinguishes this from a read-only plan. Do not retry with a new directory to skip the cause.

## Post-repair verification (still no task installation)

1. Run `-Mode Plan` again, saving a **different** report. Expect no blockers and zero changes. Confirm the original device still binds and protected-state hashes/ACLs are unchanged.
2. Compare installed code to the approved release while the connector remains stopped. Complete the ACL acceptance/rollback decision before copying code updates: content changes intentionally block automatic rollback of the old snapshot. After separate code-release approval, the strengthened `receipt_autostart.ps1` can be installed using the normal reviewed update procedure; its check is stricter, not bypassed.
3. In a normal User shell, run the original local `startup-check` only after content integrity and ACL trust are established. It must still identify Register 1 and the preserved journal.
4. Optionally dot-source the **reviewed trusted copy** of the startup helper and call its read-only checks:

```powershell
# Copy the reviewed helper into private staging first; do not modify installed code yet.
$ReviewedHelper = Read-Host 'Full path to the reviewed source receipt_autostart.ps1'
Copy-Item -LiteralPath $ReviewedHelper -Destination "$Stage\receipt_autostart.ps1"
. "$Stage\receipt_autostart.ps1"
$id = Get-ReceiptIdentity
$paths = Get-ReceiptPaths
Assert-ReceiptAcl $paths $id
```

No output/error means the strict ACL check passed. This does not register a task. Existing Phase 24 installation/logon/physical acceptance remains a **separately approved** phase. Leave cash drawer OFF and Register 2/Shopify/accounting unchanged.

## Exact, separately approved rollback

Rollback restores only exported DACLs inside the original allowed scope. It does not restore/delete files or touch ownership, credentials, journal or printers. It intentionally reintroduces the old broad-write permissions; that state is **not approved for automatic startup**, and the original security checker must reject it again. Keep tasks disabled/absent and the connector stopped.

If Apply stopped before returning a backup hash, use the already specified `$Backup\before-acls.json`; calculate its SHA-256 locally, confirm it belongs to this attempt and retain the original file. Do not use an unreviewed external backup.

```powershell
$Before = Join-Path $Backup 'before-acls.json'
$BeforeHash = (Get-FileHash -LiteralPath $Before -Algorithm SHA256).Hash.ToLowerInvariant()
$RollbackPlan = Join-Path $Stage 'acl-rollback-plan.json'
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Stage\repair_receipt_startup_acl.ps1" `
    -Mode RollbackPlan -BackupFile $Before -BackupHash $BeforeHash |
    Set-Content -LiteralPath $RollbackPlan -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw 'Rollback preflight stopped; preserve all evidence.' }
$R = Get-Content -LiteralPath $RollbackPlan -Raw -Encoding UTF8 | ConvertFrom-Json
$R.body.blockers
$R.body.changes | Format-Table -AutoSize
$R.approval_hash
```

Owner reviews the fresh rollback manifest and provides a **new explicit approval**. In an elevated same-User shell, within 15 minutes:

```powershell
$RollbackHash = Read-Host 'Paste the reviewed rollback approval_hash'
$RollbackRef = Read-Host 'Enter the genuine rollback approval reference'
$Undo = Join-Path $BackupParent ('before-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "$Stage\repair_receipt_startup_acl.ps1" `
    -Mode Rollback -PlanFile $RollbackPlan -ApprovalHash $RollbackHash -ApprovalReference $RollbackRef `
    -ConfirmTarget 'DESKTOP-ADTR67Q\User|REGISTER1|ACL ONLY' -BackupDirectory $Undo
```

It verifies the backup hash, fresh state, same path/sibling membership, unchanged contents/owners/audit-only ACLs, exports an undo backup, restores changed DACLs parent-first, and checks the entire original inventory. Success is **`ACL_ROLLBACK_VERIFIED_NO_TASK_ACTION`**. Unchanged shared parents are never rewritten. If new files/apps, outside ACL changes or owner/content drift are found, rollback stops for a separately reviewed recovery plan; there is no force override. Any interrupted/failed rollback is partial until verified—retain both backup generations.

## Acceptance and remaining limitations

- Mocked tests cover inherited/explicit broad principals, RX retention, generic rights, same-SID UAC/elevation, exact/expired approval hashes, no-overwrite exports, JSON round trips, fresh-state drift, backup-before-write, partial failure, safe shared-parent exclusion, full validation and rollback. The existing checker tests also prove launcher directory and delete-child rejection.
- Native Windows 11 inheritance propagation, DPAPI under actual UAC and Windows PowerShell 5.1 DACL persistence still require authorized acceptance. No production permissions were changed during development. Unknown/conditional/deny-heavy ACLs and unsafe external interpreter/state trees are intentionally unsupported automatic repairs.
- ACL operations are not a distributed/atomic transaction. Use the maintenance window, reviewed hashes, backups and honest post-validation; do not describe this as an atomic no-race guarantee or code-authenticity proof.

## Reviewed source checksums and automated results

For this reviewed tool revision:

| File | SHA-256 |
|---|---|
| `receipt_acl_repair.ps1` | `e41c2b4283360d14cf296884f230bc456829112915df125acb0ddbe45a4d32bd` |
| `repair_receipt_startup_acl.ps1` | `60f104a8d687a7845d216bf26c7babb898c965cea559213eed66e87ffb6a095e` |

Compare against the approved PR revision; any later code change requires new reviewed hashes and a new preflight. Do not “fix” a mismatch by editing the expected value locally without reviewing the change.

**126 tests passed; zero failures or skips** in the final ACL/startup/engine/diagnostic/GDI regression selection. This includes 9 new pytest cases, an actual PowerShell planning/rollback/checker harness with 44 assertions, and 44 retained Phase 24 startup cases. PowerShell parsing, the private DTO reader's compilation/fail-closed fixture, no-overwrite backup I/O, JSON/hash round trips and hard-link refusal were executed in isolated fixtures. Real Windows ACL APIs, actual DPAPI credentials and native inheritance restoration were mocked/not executed. Existing deprecation warnings remain. No migration or production-setting change is required.

Reproduce with an installed PowerShell 7 executable available as `pwsh` or `TEST_PWSH`:

```text
python -m pytest -q tests/test_receipt_acl_repair.py tests/test_receipt_autostart.py tests/test_print_agent.py tests/test_print_agent_diagnostics.py tests/test_windows_print_backend.py tests/test_pos80_startdoc_result.py tests/test_receipt_connector_gdi.py
```

Changed files: the existing `scripts/windows/receipt_autostart.ps1` checker is strengthened; new `scripts/windows/receipt_acl_repair.ps1` planning/native helpers, `scripts/windows/repair_receipt_startup_acl.ps1` default-read-only entrypoint, `tests/test_receipt_acl_repair.py`, `tests/windows/test_receipt_acl_repair.ps1`, and this runbook are added. No Python application, journal, transport, printer-driver, Shopify, accounting, migration or feature-flag code changes.
