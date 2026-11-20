# Windows 11 — PixiePinks Print Agent installation and recovery

**Release status:** source/build instructions only. No Windows executable/MSI was built or signed in the Linux development environment. Tests mock Windows; the actual counter setup must pass the linked acceptance checklist before any hardware gate is enabled. Never disable Defender, SmartScreen, TLS certificate validation or PowerShell security policy to install this software.

## 1. Prepare the actual USB queues

1. Confirm the DBL printer is powered, cabled directly to the intended Windows 11 counter PC and has the correct stock loaded. Inspect the existing installed driver; do not replace it arbitrarily.
2. Open Settings → Bluetooth & devices → Printers & scanners. Locate exactly **4BARCODE 3B-365B** (labels) and **POSPrinter POS-80** (receipts). They are two logical queues for one physical device.
3. In Printer properties → Ports, record the working USB/DOT4 port and confirm both queues map to the same actual local printer. Do not blindly choose USB001/USB002 based on name; identify the binding using the connected hardware, Windows status and controlled Windows test. The agent checks common port binding but cannot establish electrical compatibility from a port name alone.
4. Under POSPrinter POS-80 preferences set the installed paper width (80 mm, or 58 mm only after testing), **Cut paper per page**, **Semi Cut**, and **15 mm feed after print**, matching the owner's tested setup. Set **Cash Drawer=None** for normal operation.
5. Set drawer control **None** on the label queue too, and verify labels never open it. Select correct label dimensions, gap/calibration and print density through the existing verified driver controls. Existing 30×15 Compact — SKU & Price remains available; do not “fit to page”.
6. No real cash drawer action should be sent merely to discover an undocumented command. Its supplied voltage/pin/pulse notes are not a command specification. Use only the already tested manufacturer driver feature, with the key **Under Control**.

## 2. Isolate the drawer queue and account

A Windows administrator must create a dedicated non-admin local account, for example `PixiePinksPrintAgent`, different from cashier/browser accounts. Do not give it Shopify credentials or CRM admin login. Grant it only the local access needed to print through these queues and run the agent.

Add a **logical** local queue named exactly:

```text
PixiePinks Drawer Control (POS-80)
```

Use the existing tested POS-80 driver and the same verified USB port. Do not add/pretend a second physical printer. Configure **only this queue** with the manufacturer driver’s tested Cashbox #1 after-print setting. Verify cutter/feed/media choices through supported dialogs. The integration emits a small non-financial control slip; accept the extra strip/cut before using it live.

Printer properties → Security on the drawer queue:

- Retain administrator/SYSTEM management rights.
- Allow Print for the dedicated agent account.
- Remove broad Everyone/Users Print grants from this queue; cashier/browser users must not be able to select it.
- Do not add a blanket Deny Everyone rule, which would also deny the agent.
- Keep normal receipt/label queues accessible according to business policy, with Drawer=None.

Windows permissions are per account, not per executable. Running the agent and browser as the same user cannot make this queue agent-only. Keep drivers installed machine-wide and configure/test the effective **agent account's** printing preferences (or supported global Printing Defaults). No registry edits or arbitrary driver replacement are required.

## 3. Build, inspect and sign on Windows

Use a clean Windows 11 x64 machine, reviewed source checkout and Python 3.12. From the repository root, run the reviewed script:

```powershell
.\windows_print_agent\build.ps1
```

It creates a separate venv and an **unsigned onedir build** in `dist\PixiePinksPrintAgent`. It does not pair, deploy, print or open the drawer. Review dependencies, scan/test the complete bundle, establish the business signing certificate, and sign the executable using the authorized Windows signing process. Record the final executable SHA-256, SHA-256 of the complete distributed ZIP/bundle, and publisher certificate thumbprint in release records. Verify the complete bundle against that trusted release hash before extracting it into an administrator-owned directory (for example Program Files) where cashier/agent accounts have read/execute, not write, access. A signed executable alone does not authenticate adjacent DLLs. No signing certificate or credentials are provided/embedded by this repository.

An unsigned build is not a production installer. For reviewed source development only, manually run `python -m windows_print_agent` in the dedicated account's venv. Do not weaken OS protections to install unsigned automation. Retain third-party notices/licenses with redistributed DLLs and the bundle.

## 4. Pair under the dedicated account

After separately approved CRM deployment/migration, keep checkout/refund settings unchanged. An administrator adds the three new hardware flags as **false** first and configures the device. Pairing/native dispatch requires at least one authorized hardware gate; any later temporary enablement for acceptance is an operator release decision, not performed by this change.

In POS → Hardware:

1. Enroll **PixiePinks Main Counter PC** against the existing **PixiePinks Main Counter** register and its real **Magammana West**, `gid://shopify/Location/92690219311`, location. If the live register has a different existing name, select that actual register; do not fabricate a Shopify location. Create registers through the existing POS admin flow only when necessary.
2. Copy the one-time pairing code, valid for five minutes.
3. Open a terminal under the dedicated agent account. For example use Windows Run as different user / supported `runas /user:.\PixiePinksPrintAgent "powershell.exe -NoProfile"`; do not use `/netonly`. Windows must load that user's profile for DPAPI.
4. Run the signed executable (or reviewed source venv):

```powershell
.\PixiePinksPrintAgent.exe pair --server https://YOUR-APPROVED-CRM-HOST
.\PixiePinksPrintAgent.exe printers
```

The pairing code is entered through a hidden prompt, not a shell argument. The response token is stored using user-bound DPAPI under `%LOCALAPPDATA%\PixiePinks\PrintAgent\credentials.dpapi`; it is never printed or embedded in a package. Use the exact existing approved HTTPS CRM origin, without path/query, and normal certificate validation. Never copy Shopify secrets, admin cookies or production environment files to the PC.

5. The administrator activates the paired device, assigns the fixed receipt/label queues, chooses 58/80 mm stock policy and optionally the isolated drawer queue. Confirm drawer verification only after its local driver/ACL test. Turn on per-device automatic receipts only for newly completed future sales. Optional A4 requires a separate suitable printer, not the DBL receipt queue.

## 5. Attest driver settings, choose media and start

Stop any running agent instance before attestation. Under the same dedicated agent identity:

```powershell
.\PixiePinksPrintAgent.exe status
.\PixiePinksPrintAgent.exe attest
```

Inspect Windows preferences/ACLs before answering YES. Attestation records effective per-user driver/private-mode fingerprints, USB queue association, normal Drawer=None, cutter/media tests and the isolated drawer mechanism. **Attest sends no print/drawer action**. Changed drivers/ports/preferences block future work until reviewed and attested again. This does not magically detect undocumented external registry/firmware changes.

In CRM Hardware, physically install stock and confirm **Receipt Mode** or **Barcode Label Mode** after checking both queues are drained. The agent acknowledges the request only after local verification and readiness. Receipt width must match the device policy. It does not detect paper automatically. For optional A4, explicitly confirm use of the separate attested A4 printer.

Run manually for acceptance:

```powershell
.\PixiePinksPrintAgent.exe run
```

Use CRM's non-financial receipt printer test; verify paper, feed/cut, and drawer stays closed. Generate/preview-confirm an existing barcode label batch and choose **Print using PixiePinks Local Printer**; scan the physical label in POS and verify exact value/variant. No test needs a real Shopify transaction. An authorized drawer test requires an open assigned register, explicit drawer permission/reason, approved gate and locally verified isolated queue. That command is a real physical action and must be performed only by the authorized Windows operator after acceptance approval.

## 6. Startup

After successful manual/physical acceptance, a Windows administrator may run the reviewed, signed startup script:

```powershell
.\windows_print_agent\install-startup.ps1 -AgentExe 'C:\ApprovedPath\PixiePinksPrintAgent.exe' -AgentAccount '.\PixiePinksPrintAgent' -ExpectedSha256 'REVIEWED-EXE-SHA256' -ExpectedSignerThumbprint 'REVIEWED-CERTIFICATE-THUMBPRINT'
```

It verifies the executable hash/signature, prompts locally for the dedicated account's Task Scheduler credentials and registers an AtStartup task named **PixiePinks Print Agent**, with one instance and bounded restart. Windows Task Scheduler protects the account password; it is not saved by the application/script. Do not paste any password in chat, source, task command arguments or logs. The account needs the Windows batch-logon rights required by Task Scheduler; configure through normal local policy, not by disabling security.

The task must use the same account/profile that paired and attested. Physically test unattended GDI printing after logoff and reboot; some drivers may require vendor-supported unattended settings. The script does not turn on CRM/Shopify flags or start a live sale. No signed executable/installer was produced here.

## 7. Offline / USB / spooler recovery

When Windows shows POSPrinter POS-80 **Offline**:

1. Stop new printing and inspect both DBL queues plus the control queue. A COMPLETE sale remains paid/complete. Do not start another sale to replace a missing receipt.
2. Check power, USB cable/port, device visibility and actual stock. Do not repeatedly send drawer tests.
3. Verify each queue's Printer properties → Ports binding and driver association. A cable moved to a different USB socket may create another Windows port/queue. Both normal queues and the isolated control queue must still refer to the verified same local USB device.
4. Review **Use Printer Offline**, **Pause Printing**, paper-out/jam/door and pending jobs. Do not assume an empty queue means paper was printed or the drawer stayed closed.
5. Under the agent identity run `status` and `printers`. These show queue-reported state, pending count and driver/port binding, not customer/payment contents or tokens. Check Windows Event Viewer → Microsoft-Windows-PrintService Operational/Admin logs when needed.
6. Stop the agent task before resolving uncertain actions. Inspect actual paper/drawer and correlate Windows documents named `PixiePinks <hardware UUID>` with CRM history. One multi-page PDF can have several Windows spool IDs. Review/cancel only known authorized jobs when appropriate; never blindly purge every queue. A partial barcode batch needs a fresh preview-confirmed PDF for remaining copies, not an automatic replay.
7. If genuinely necessary and approved by the Windows administrator, restart the Windows **Print Spooler** through Services or elevated `Restart-Service Spooler`. This affects all local printers; first inspect/record pending work. Restarting the spooler does not prove prior jobs were unprinted. Do not delete spool directories/registry keys as a routine remedy.
8. Restore normal receipt and label Drawer=None, receipt cutter Semi Cut/per page and 15 mm feed, correct paper dimensions/gap and queue binding. Reattest any changed fingerprint and confirm media. Resume the agent only after reviewing any UNKNOWN server/local action. A submitted/uncertain drawer command is never replayed automatically.

## 8. Rotation, upgrades and removal

- CRM Rotate invalidates the current credential and gives a new one-time code. Stop the task, re-pair under the same account, review outstanding actions and reactivate. Keep `%LOCALAPPDATA%\PixiePinks\PrintAgent\actions.sqlite` and its WAL/SHM files; never delete it to “fix” duplicate suppression.
- Revoke a stolen/retired device immediately. Revocation blocks future auth and marks possibly delivered work uncertain; it cannot retract bytes already sent to Windows. A revoked ID needs a new enrollment. Local old fences remain retained and do not dispatch for a new device identity.
- Upgrades are manual reviewed/signed releases with hash/publisher verification; there is no self-update/downloader/remote shell. Stop the task, preserve the user profile/DPAPI/action journal, replace the approved complete bundle, revalidate driver settings as needed and repeat acceptance. Never change device IDs/attempt state just to resend an unknown action.
- Uninstall: deactivate/revoke in CRM, stop and unregister the **PixiePinks Print Agent** scheduled task, then run `forget` under the paired identity to delete local credential/attestation blobs. Retain/archive the action journal under the approved retention policy. Remove the agent executable/venv and optional isolated drawer queue through Windows administration after pending work is reconciled. Do not remove the existing label/receipt queues or finance data as part of agent removal.

## Isolated drawer transport prerequisite

The administrator must set the isolated drawer queue to **Print directly to the printer**, turn **Keep printed documents** off, and obtain manufacturer-supported evidence and authorized physical interruption tests showing that USB/spooler recovery does not replay the driver cashbox action. The agent checks the direct/no-retention queue attributes and requires a separate local no-replay attestation. Unknown or unsupported behavior leaves drawer automation blocked. To accept receipts and labels independently, clear the isolated drawer queue and drawer verification in the CRM device policy, then attest the normal queues only. The application journal prevents application retries, but cannot establish exactly-once physical behavior inside a Windows driver or printer firmware. No physical interruption test or drawer action was performed during development.
