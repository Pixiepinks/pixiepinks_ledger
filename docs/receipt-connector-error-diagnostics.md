# Receipt connector: diagnostic-only error reporting

Based on PR #190, release `35d29d6fa7fdf36bd5125c1ccee8df434c85f232`.
This change adds local structured stderr records. It does not repair a driver,
change dispatch authorization, extend deadlines, retry printing or resolve jobs.

## What becomes observable

Examples (illustrative, not production findings):

```json
{"event":"connector_error","stage":"permit_response","exception_class":"ReadTimeout"}
{"event":"connector_error","stage":"progress_heartbeat","exception_class":"HTTPStatusError","http_status":403}
{"event":"connector_error","stage":"gdi_start_doc","exception_class":"OSError","windows_error_code":5}
{"event":"connector_error","stage":"journal_submitted","exception_class":"OperationalError"}
{"event":"connector_recovery","stage":"journal_crash_recovery","recovered_intent_count":1}
```

| Stage group | Interpretation |
|---|---|
| `permit_response`, `permit_deadline` | Permit RPC/response validation versus the existing five-second limit |
| `heartbeat`, `progress_rpc`, `progress_heartbeat` | Poll heartbeat versus the in-submission progress and heartbeat calls |
| `driver_fingerprint`, `driver_mode`, `driver_create_dc`, `driver_wrap_dc`, `driver_capabilities`, `driver_geometry`, `driver_margins` | Driver snapshot, temporary DEVMODE, device-context creation and existing safety checks |
| `pdf_backend_import`, `pdf_open`, `pdf_page`, `pdf_render`, `pdf_image_conversion`, `pdf_cleanup` | PDF/rendering setup, rasterization, conversion or cleanup |
| `gdi_start_doc`, `gdi_start_page`, `gdi_draw`, `gdi_end_page`, `gdi_end_doc`, `gdi_release`, `gdi_abort_doc` | Exact GDI call or cleanup that failed |
| `journal_intent`, `journal_submitted`, `journal_unknown`, `journal_pending`, `journal_ack` | Local durable intent, outcome persistence and acknowledgement persistence |
| `outcome_ack`, `configuration_fetch`, `job_claim`, `printer_status`, `attestation_check`, `dispatch_lock` | Other network, local verification and printer-lock failures |

The shared diagnostic scope reports the innermost classified failure once as the
same exception propagates. A second cleanup/persistence exception can produce a
second record. Generic `job_execution`, `driver_submission`, or `poll_cycle` labels
cover exceptions outside the finer-grained boundaries. A 409 acknowledgement
record can accompany the existing handled "already resolved" behavior; it does
not authorize a resend. Silence is not evidence of physical success.

Records contain only fixed event/stage labels, allowlisted exception-class labels,
and optional bounded numeric Windows/HRESULT/OS/HTTP codes. Unknown exception
classes become `OtherException`; unexpected stage names become `unclassified`.
No exception message, args, traceback, URL, headers, body, PDF, credential, pairing
code, private key, customer detail, job UUID or device UUID is emitted. Recovery
records contain only a positive count of INTENT rows converted by existing recovery.
Numeric codes may be absent if the exception provides no suitable attribute.

Logging is best effort and re-raises the original exception. A failed diagnostic
sink does not replace it or bypass journaling. No new log file, database column,
endpoint, transport field, dependency, flag, print retry or background worker exists.
Stdout's existing generic CLI messages remain; structured records are on stderr.
No durable diagnostic history is created automatically. An approved operator may
capture console output under existing local access/retention policy; never enable
HTTP/body tracing or raw traceback dumps as a workaround.

## Preserved safety semantics

- Original intent identity, SQLite schema/WAL/FULL durability and attempt count are
  unchanged. Intent still precedes permit and any driver submission.
- Original five-second permit limit and all queue/media/geometry/fingerprint gates
  remain unchanged. The encrypted transport and server are untouched.
- Exceptions inside the existing protected dispatch block still attempt UNKNOWN
  persistence. No failure is downgraded to a safe retry, even before StartDoc.
- A crash leaves INTENT; startup still converts INTENT to UNKNOWN. The only addition
  is a count after the existing update is committed. Existing UNKNOWN rows are not
  reclassified or given invented historical diagnostics.
- Acknowledgements can still be resent idempotently; driver submission cannot.
- The existing UNKNOWN with a null spool ID still blocks later claims for that
  receipt-only device. Logging does not change server claims or review eligibility.
- Null spool ID remains inconclusive: the baseline can lose the spool ID when an
  exception occurs after StartDoc/EndDoc or during local success persistence. This
  diagnostic patch intentionally does not change those historical semantics.

## Existing incident: what cannot be reconstructed

Job `2e4f9c11-ba4c-4d7e-907e-a6aed6bdb9d1` is already UNKNOWN and locally acked.
The original exception was discarded by the old CLI handler. New logging cannot
recover its stage, Windows error, actual physical outcome or whether it reached
StartDoc. The pre-StartDoc sample passing at 203 DPI does not identify network
progress failures or later GDI/journal failures. No historical row is modified.
The two queued TEST jobs stay queued; no new test is created by this patch.

## Review and deployment (not performed)

1. Review and approve this diagnostic-only change. No production attempt or recovery
   is authorized by the PR. Keep automatic finalized receipts and drawer automation
   disabled; preserve Shopify/POS settings and the separate POS REVIEW operation.
2. Arrange an explicit Windows connector maintenance window. Under the approved
   stop procedure, ensure no driver call is still in flight; inspect existing job
   and Windows-queue state. Do not force-kill in-flight printing to install this.
3. Retain the original code revision and all existing DPAPI/journal files. Follow
   the existing backup policy for a stopped SQLite WAL database; never copy only
   the main file while an active WAL is being written, purge it or initialize a new
   journal to diagnose this incident.
4. Update the reviewed connector code/package to the approved feature commit using
   the same Windows identity, Python environment and install path. Include the new
   `windows_print_agent/diagnostics.py` module. No dependency reinstall, pairing,
   credential rotation, attestation change, printer-preference edit, Railway
   variable, database migration or backend deployment is required by this patch.
   An executable distribution requires the usual reviewed Windows build; no signed
   executable or installer has been produced by the Linux test environment.
5. Starting the connector again requires operational approval. Startup retains the
   existing INTENT recovery behavior; this is not a read-only diagnostic command.
   With the incident UNKNOWN intact, normal claims remain fenced. Do not expect a
   restart to re-diagnose the failed attempt or print queued jobs.
6. A future authorized attempt requires separate physical uncertainty review and
   queue recovery authorization. Do not click review/reprint, clear UNKNOWN, queue
   another test or run a print attempt merely to obtain a diagnostic record.

## Rollback

After explicit rollback approval and the safe stop/queue inspection procedure,
restore the prior reviewed Windows connector code/package in the same path and
under the same identity. Preserve the complete existing journal, credentials,
attestation, server jobs and audit records. The journal format is unchanged and
readable by the previous release. Rollback does not resolve UNKNOWN or authorize
queued work. No Railway flag or schema rollback is needed.

## Validation

All Windows/GDI/spool operations are mocked; PDFs and journals in tests are
synthetic/disposable. Coverage includes before/after StartDoc, successful EndDoc
followed by cleanup failure, rendering/driver/permit/progress failures, redaction,
failed logging sinks, journal-write failures, crash recovery and no replay.
Actual Windows hardware execution and production incident recovery are deferred.

Results: the combined connector, encrypted transport, proxy, hardware, finalized
receipt, branding/geometry and provisional receipt run passed **194 tests**.
The final focused diagnostic suite passed **32 tests**, including three additional
status/mutex/silent-success checks (197 distinct cases across the runs). No failed
or skipped tests. Existing suite deprecation warnings remain. Python compilation
and `git diff --check` passed. No PostgreSQL rerun was required for this client-only
change: server code, database schema, migrations and SQL state transitions are
unchanged. No physical Windows test or production operation was executed.
