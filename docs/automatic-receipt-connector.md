# Automatic finalized 80 mm receipt connector — review and release runbook

Status: implementation for review; **not deployed, activated or physically tested**.
Migration: `20261121_49` (parent `20261120_48`). No financial data migration.

## Architecture and security audit

The existing `hardware_jobs` queue, `hardware_events` audit, background worker,
COMPLETE-only `pos_receipts` generator, single-use dispatch permits, device pairing,
Windows DPAPI credentials, durable SQLite action journal and PDFium/GDI backend
already implement most of the required lifecycle. They remain the only hardware
job system. The server worker scans durable COMPLETE outcomes; browser events are
not responsible for original enqueue. A shop lock and unique business key
`receipt:auto:<original checkout UUID>` prevent duplicate originals across workers,
sessions, policy changes and restarts. This is deliberately stronger than allowing
another original for every policy revision. Receipt generation is downstream of
financial completion, rather than part of its posting transaction. Generation
failure reserves the key as FAILED for operator review, never a retry loop.

Missing pieces addressed here: a dedicated receipt-only policy independent of the
DBL label printer, secure transport usable on shared Railway, physical-test-gated
automatic enablement, pause/resume, controlled duplicate receipt lineage, additional
Windows status evidence and approved startup installation instructions.

### Trust model requiring release/security approval

Railway's shared private network does not prove edge origin. The old native routes
still reject untrusted HTTP; Uvicorn remains `--no-proxy-headers` and
`print_agent_security.py` is unchanged. Do not enable isolated ingress, add wildcard
proxy trust or trust forwarded headers to solve this. The new
`POST /api/print-connector/envelope` intentionally treats its HTTP hop as untrusted;
it makes **no claim to prove that TLS terminated at a particular Railway edge**.

The connector initiates outbound certificate-verified HTTPS, disallows redirects
and environment proxy inheritance, and pins an independent application public key
copied from existing authenticated CRM Admin. Each request uses ephemeral X25519,
HKDF-SHA256 domain separation and separate AES-256-GCM request/response keys. AAD
binds the canonical request UUID; timestamp, origin, pairing/bearer credential and
operation body are inside encryption. The response is bound to that request and
server key. An independently committed PostgreSQL UUID replay fence survives
handler failure/lost responses and competing processes. Requests expire after
60 seconds (clock tolerance both directions); nonces remain for at least 3 minutes.
Keep Windows/server clocks synchronized using the approved OS time service; a skew
over 60 seconds fails closed. Pairing remains five-minute/single-use; device activation, shop/register scope,
credential rotation/revocation, throttles, actor authorization and job permits
remain enforced. Only receipt/test operations are exposed; no financial operation,
label, drawer, remote path, arbitrary command or inbound Windows port exists.

This is a new application security protocol, not a substitute for a security
review or evidence of physical print success. Approve it separately before enabling.
Review the implementation and key lifecycle; protect the server private key in the
approved secret store and never expose it to browsers/logs/chat. Disclosure of this
key compromises this tunnel, including recorded private-hop messages (no independent
forward secrecy against later server-key compromise). Outer HTTPS is also mandatory.
A compromised authorized Windows identity can steal its device token and receipt
contents; use restricted account/ACLs. Other private-network services can still cause
availability attacks, but forwarded headers do not authorize access or disclose
plaintext. An attacker can race an exact captured envelope, causing denial of that
request, not obtaining its encrypted response or an additional dispatch permit.
Existing authenticated CRM Admin and secret-store access are trusted; do not obtain
the pin by unauthenticated discovery. Ensure access logs do not record bodies or
query credentials. This implementation logs neither decrypted payload nor errors
containing credentials/customer payment contents. Operational monitoring should use
job/device identifiers and sanitized status codes.

Limits: 64 KiB envelope request, 8 MiB receipt PDF, 16 MiB encrypted response,
240 requests/min per socket peer and active device, 12 pairing requests/min per
device. Reverse proxy peers may share that budget; validate with actual deployment
scale. Requests with browser Origin headers are rejected; native public-key
possession alone is not device authentication. Private callers still need the
one-time code or device credential, encrypted to the pinned server.

## Exact configuration contract

These are proposed values for **separately approved deployment**, not changes made
by this work. Defaults remain off/empty. Inspect current values in the authorized
runtime; this development task has no production configuration evidence.

| Variable | Required for this connector | Default / handling |
|---|---|---|
| `POS_ENABLED` | `true` for existing POS | Existing POS gate; do not toggle to bypass a blocked queue |
| `POS_LOCAL_PRINT_ENABLED` | `true` after release approval | `false`; gates receipt dispatch and new endpoint |
| `PRINT_CONNECTOR_ENCRYPTED_ENABLED` | `true` after security/release approval | New, `false` |
| `PRINT_CONNECTOR_PRIVATE_KEY` | 32 random X25519 private bytes encoded as exactly 64 hex characters | New, empty; secret, excluded from settings repr |
| `PRINT_AGENT_PUBLIC_ORIGIN` | `https://pixiepinksledger-production.up.railway.app` | Exact approved HTTPS origin; no path/query/userinfo |
| `POS_CASH_DRAWER_AUTOMATION_ENABLED` | Keep `false` | Do not enable |
| `PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED` | Keep `false` on shared Railway | Not used by encrypted path |
| `PRINT_AGENT_TRUSTED_PROXY_IPS` | Leave existing configuration untouched | Not used by encrypted path |
| `RETAIL_HARDWARE_LABEL_PRINT_ENABLED` | Not required | Leave existing barcode configuration unchanged; this path never accepts labels |

No Shopify scope, write flag, webhook, import, Draft Order or financial setting is
required by printing. The existing server retail worker must run (its existing
hardware tick creates/ages jobs). Device `auto_receipts` defaults false, separately
managed by CRM Admin. No new global auto-provisional or auto-reprint flag exists.
Generate the private key using an approved cryptographic tool/secret-manager under
administrator control; inject directly through approved secret handling, never a
repository file or shell command that displays the secret. The public pin is shown
only through the authenticated admin `connector-transport` API/UI. Key rotation
requires pausing, stopping the connector, reviewing uncertain jobs, updating the
secret and re-pairing/re-pinning every approved connector; never silently accept a
replacement pin. Do not rotate without an operational approval/window.

## Reviewed deployment sequence (not performed)

1. Review/approve draft PR and transport design, then merge/deploy only through the
   authorized release process. Back up as usual; run `alembic upgrade head` through
   the existing migration process. Migration adds only `print_connector_nonces`.
2. Initially keep new/receipt gates false. Confirm application health and existing
   POS, provisional PDFs, labels, WhatsApp and worker operation. Existing native
   TLS/proxy checks are unchanged; the old endpoint's 403 is not a release failure
   for this new transport.
3. After transport approval configure its key/origin and explicitly approved gates.
   With a valid encrypted envelope but no device credential the new path returns
   encrypted 401; it does not expose a replacement unauthenticated config endpoint.
4. Open CRM → Point of Sale → Hardware (existing status button). Select the existing
   register/location, enroll a **dedicated 80 mm receipt connector**. The UI sets
   receipt-only and automatic false before displaying its five-minute code.
   Copy the non-secret public key from this authenticated admin view.
5. Complete Windows installation, pairing, physical acceptance and pilot below.
   Only an administrator can enable automatic final receipts. Server enforcement
   requires a connected ACTIVE receipt-only device, locally verified RECEIPT media,
   and a physically CONFIRMED synthetic TEST at the current configuration version.
   Configuration changes reset media confirmation; reconfirm it before dispatch.

## Windows installation and startup

Use one approved Windows shop PC, existing `POSPrinter POS-80` queue, its current
vendor driver and local USB port. Do not rename/rebind/reinstall queues automatically.
DBL 365B remains independent. Inspect Windows printer preferences: 80 mm roll,
portrait, actual-size layout, no scaling, **Cash Drawer=None**, correct paper feed
and cutter. The backend copies a per-job DEVMODE; it does not call SetPrinter or
change global settings. Actual receipt page height drives the custom job paper
height. It checks 80 mm width, one page, 20–1500 mm height and that physical hardware
margins can cover the existing 5 mm receipt safe region; incompatible drivers fail
before StartDoc. Silent dispatch uses PDFium/GDI and Windows spool IDs, no dialog,
raw USB writes or automatic drawer commands.

IT administrator installs a reviewed checkout/build of this commit in a directory
writable only by administrators and readable by the restricted connector OS account.
Create/use an approved restricted Windows account with printer access and batch
logon rights; no CRM password or separate application login is stored. Pairing,
attestation and startup **must use the same Windows identity/profile**, since
credentials are CurrentUser DPAPI in `%LOCALAPPDATA%\PixiePinks\PrintAgent`.
Protect that directory/action journal with account/admin-only ACLs. Do not copy,
reset or delete `actions.sqlite` during repair; it is a durable no-replay fence.
Do not run two PCs sharing an action journal or device credential.

Example reviewed-source installation (PowerShell, replace paths for approved PC):

```powershell
cd C:\PixiePinks\Connector
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r windows_print_agent\requirements.txt
.\.venv\Scripts\python.exe -m windows_print_agent printers
.\.venv\Scripts\python.exe -m windows_print_agent pair --server https://pixiepinksledger-production.up.railway.app --public-key PUBLIC_HEX_FROM_AUTHENTICATED_ADMIN
```

The code is entered at a hidden prompt, never on the command line. Activate the
paired device in CRM Admin, then under that same Windows identity:

```powershell
.\.venv\Scripts\python.exe -m windows_print_agent attest
.\.venv\Scripts\python.exe -m windows_print_agent status
.\.venv\Scripts\python.exe -m windows_print_agent run
```

Attestation sends no print. Inspect queue readiness, Cash Drawer=None, physical
paper/cutter settings and USB mapping before confirming prompts. Receipt-only
attestation requires only POS-80; it neither requires nor modifies the label queue.
CRM Admin requests RECEIPT media; connector verifies empty ready queue and its
fingerprint before acknowledging. Use the synthetic test below while auto is false.
Stop the foreground process before installing startup. No Windows executable or
service has been built/physically certified by Linux CI; do not bypass Windows
security warnings or purchase a certificate as a workaround.

After physical pilot approval, Windows Admin may register the supplied Task Scheduler
installer, supplying a restricted **OS** PSCredential through `Get-Credential`:

```powershell
.\windows_print_agent\install_startup.ps1 -PythonPath C:\PixiePinks\Connector\.venv\Scripts\python.exe -ProjectRoot C:\PixiePinks\Connector -ServiceCredential (Get-Credential)
```

This creates `PixiePinks Receipt Connector`, limited privileges, AtStartup, single
instance, StartWhenAvailable and bounded restart interval, no immediate start. It
refuses to overwrite an existing task. **The next reboot will trigger it**; install
only after startup activation is approved. Password is passed only to Windows Task
Scheduler, not saved by the script. Confirm the account profile/LOCALAPPDATA and
DPAPI work noninteractively; approve batch-logon policy/queue ACLs separately.
Start the task explicitly only after approval. Verify its last-run result and CRM
heartbeat, restart Windows, then verify again. Network failure loops reconnect
without resending a locally journaled action. The existing machine-wide printer
mutex is retained for compatibility with older agents; it may serialize independent
queues on this PC rather than allowing competing connector processes. No staff interaction/login is needed
for normal operation after this one-time administration.

## Physical acceptance and controlled production pilot

No physical tests were executed in development. Record operator/time/device/register,
configuration version, job ID and spool ID. Never use the two historical test sales.

- With automatic false and drawer false, request RECEIPT media. Queue **one
  non-financial synthetic receipt** in CRM Hardware. Verify one 80 mm correctly
  aligned legible paper, existing branding, feed/cut, and that DBL/drawer are idle.
- Windows acceptance yields SUBMITTED_TO_PRINTER only. Inspect actual paper, then
  record CRM physical confirmation/reason. A spool ID is not physical confirmation.
- Test paper-out/offline/USB disconnect **with synthetic documents only**, reconnect
  and inspect Windows queue before permitting further actions. Verify known status
  conditions appear. Drivers may omit or delay status; no physical certainty follows
  from READY. Stop/inspect uncertain jobs; never assume reconnect means safe resend.
- Restart connector/Windows/backend; ensure the same journal/job remains fenced,
  no second original and no unattended reprint. Test network loss and reconnection.
- Test pause retains QUEUED work and blocks claim/permit; resume after inspection.
  Already permitted/in-flight GDI work cannot be recalled; use OS stop/queue review
  if emergency stop is needed. Revoke/rotate blocks subsequent authentication;
  receipt-only rotation disables automatic printing pending new pairing/pilot.
- Administrator enables automatic finalized receipts for this register only after
  current-version synthetic physical confirmation. Reconfirm media after save.
- Separately approve exactly one genuine valid checkout. Require verified COMPLETE
  through normal Shopify/accounting processing; inspect one auto job, one spool ID
  and one actual receipt. Confirm no duplicate original across browser refreshes or
  cashier sessions. Record physical confirmation in CRM.
- If the old same-shop REVIEW operation still blocks processing, **stop the real-sale
  pilot**. Printing cannot repair that incident, authorize checkout or manufacture
  COMPLETE evidence. Resolve it only under its separate approved recovery process.

## Outcomes, recovery and reprints

Existing states are reused: QUEUED → DELIVERED_TO_AGENT →
SUBMITTED_TO_PRINTER → operator CONFIRMED; FAILED / UNKNOWN are review outcomes.
A locally durable intent precedes consuming the single-use server permit and GDI.
Attempt count is one. Lost acknowledgements can report the same journaled outcome
again; they do not re-submit to GDI. Interrupted intent becomes UNKNOWN, not retry.
Multiple cashiers cannot claim originals independently; active register/device,
shop lock, job key, claim and local process lock enforce coordination.

**Neither this software nor the spooler can guarantee exactly one physical sheet.**
Windows/vendor drivers can resume already submitted jobs after USB/power recovery.
An UNKNOWN job requires stopping/inspecting the connector and Windows queue before
CRM review. Use notes to record what was actually observed. After original status
is CONFIRMED or inspected FAILED, authorized staff at the register can choose
'Authorize duplicate receipt', provide a reason and explicit confirmation. It creates
a distinct idempotent job with parent lineage and a visibly DUPLICATE PDF. UNKNOWN
cannot be reprinted until reviewed; receipt-only uncontrolled manual agent duplicates
are rejected. Repeated API calls with the same reprint key return the same job/audit.
Never delete server audit or local fences to force another original.

Auto scan intentionally covers completions after policy enablement and within the
last 24 hours; queued jobs expire after 24 hours. Old/expired/failure-fenced work
requires deliberate review, not backfill or automatic resend. Receipt PDF payloads
expire after seven days under the existing policy; audits remain. No new customer
transaction/payment/stock/journal is created by printing. Provisional acknowledgements
remain explicit, genuine-payment-verified and never automatic. Known historical
test references remain excluded. Source-level POS completion and finalized receipt
safeguards are unchanged.

## Rollback

Use separate release approval. Pause receipt-only devices in CRM; stop the Windows
task/process and inspect in-flight Windows jobs before any reprint. Disable the new
connector and local receipt gate through the authorized configuration process only
if approved (this task changes neither). Roll back application code through normal
release tooling; retain nonce, hardware job/audit tables and Windows SQLite journal.
Migration downgrade intentionally drops nothing. Existing legacy rows remain valid:
new receipt-only/pause config fields default false when absent. Do not route these
devices through legacy endpoints to work around disabled transport. Manual finalized
browser PDFs remain available for eligible COMPLETE sales. Never force-complete a
sale, replay financial jobs, purge queues blindly or erase uncertain dispatch history.
