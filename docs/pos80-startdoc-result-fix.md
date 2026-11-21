# POS-80: successful StartDoc with no exposed spool ID

Baseline: PR #191, `105ab19588543ac9957d967d1c6fbe8bd841d7f7`.
This is a narrowly scoped Windows connector correction, not production recovery.

## Reproduced cause

The requirements pin `pywin32==311`. Its `PyCDC.StartDoc` wrapper calls the native
StartDoc implementation, checks for a failure, then returns Python None rather
than exposing the native job ID:

https://github.com/mhammond/pywin32/blob/b311/Pythonwin/win32dc.cpp#L1932

The baseline `WindowsPrinters.submit` saved that None as `jobid`, completed
StartPage/drawing/EndPage/EndDoc and cleanup, then called `int(jobid)` at
`windows_print_agent/windows.py:147`. `int(None)` raises TypeError. The outer
`driver_submission` diagnostic is therefore expected, even after paper output.
Engine.execute then followed its existing UNKNOWN path with a null spool ID.

Before changing code, the new `test_startdoc_binding_result_after_successful_enddoc[None]`
failed against unchanged baseline code at that exact conversion:

```text
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'NoneType'
{"event":"connector_error","stage":"driver_submission","exception_class":"TypeError"}
```

The test uses actual in-memory PDFium rendering and mocked Windows/GDI calls, with
a StartDoc mock returning None according to the pinned binding contract. It submits
no physical job. Earlier GDI mocks returned an integer (77), hiding this mismatch.

This establishes a reproducible source/dependency defect matching the reported
post-print failure. No Windows exception traceback was recovered for historical
job `b45a877f-e8ef-4609-93c8-d41e91b9e29a`; this patch does not invent historical
evidence or claim physical production acceptance has already passed.

## Minimal correction and preserved behavior

After successful GDI completion, `submit` now returns None when the binding exposes
no job ID, and retains the existing integer conversion for bindings/test doubles
that return an ID. It does not fabricate an ID, query the spool queue to guess one,
call StartDoc again, change driver settings or introduce another print mechanism.
The existing journal and encrypted acknowledgement already accept a null spool ID.

Expected new successful outcome with this binding:

- Local state: SUBMITTED_TO_PRINTER; spool_id: null; acked: 1 after acknowledgement.
- Server status: SUBMITTED_TO_PRINTER; AGENT_SUBMITTED_TO_PRINTER audit event.
- physical_output_verified remains false until separate operator confirmation.

A null ID alone is not failure, and successful spooling is not physical verification.
Submission, page, draw, EndDoc, cleanup and journal exceptions still follow the
existing UNKNOWN behavior. The return conversion has one additional allowlisted
stage, `gdi_submission_result`, to classify unexpected binding return types without
logging their values or raw error messages.

No engine/journal/transport/authentication/server/schema/settings change is needed.
Durable intent still precedes the single-use permit; identity and UNKNOWN fences
remain unchanged. Neither this correction nor rollback replays old jobs. Barcode
queue routing, receipt-only isolation and cash-drawer gates remain untouched.

## Tests

The focused suite covers:

- pywin32's successful None return, plus retained integer-return behavior.
- Exactly one StartDoc through successful EndDoc.
- Durable success/acknowledgement with a null spool ID through the real encrypted
  TestClient protocol, without a financial transaction or drawer job.
- Restart and duplicate delivery without a second dispatch.
- EndDoc/cleanup failures with a None-returning binding still becoming UNKNOWN.
- Unexpected return-type diagnostics and continued no-replay protection.

The baseline failure was reproduced before the correction. All seven focused
cases pass after it. The broader GDI, structured-diagnostic, crash/journal,
connector, encryption, proxy, hardware and receipt regressions are recorded below.
All Windows/spooler calls are mocked; databases and journals are disposable fixtures.

## Deployment (requires separate approval; not performed)

1. Review/merge the draft PR through the normal approved release process. Do not
   merge/deploy as part of diagnosis. Record the approved fix commit.
2. Arrange an authorized Windows connector maintenance window. Verify no print is
   in flight before stopping; do not force-kill uncertain driver work. Retain the
   existing install path, OS identity, Python environment, DPAPI files and complete
   SQLite journal/WAL history under the established backup policy.
3. Update the reviewed Windows connector package to the fix commit. Include both
   `windows_print_agent/windows.py` and `windows_print_agent/diagnostics.py` from
   that commit. Do not mix file versions or upgrade pywin32 as a workaround.
   No dependency reinstall, Railway configuration change, backend deployment,
   database migration, pairing, attestation reset or printer-preference edit is
   required by this patch. A packaged executable needs its normal reviewed Windows
   build; none was physically built/tested here.
4. Do not resolve or replay the existing UNKNOWN job, release its queue fence or
   dispatch the remaining queued job. Starting updated code does not diagnose or
   fix the historical record. Keep automatic receipts and drawer automation off.
5. A start/test/recovery requires its own operational approval. Existing background
   polling may run when the agent starts; the original uncertainty fence must not
   be bypassed merely to exercise this fix.

## Safe physical acceptance — future separate authorization

Prefer an isolated development/test backend and device with only one newly approved
non-financial TEST job and its own test journal/credentials, rather than any existing
production job. Use the approved receipt-only POS-80 configuration and existing
vendor driver/USB assignment. Do not use the barcode or drawer queue.

If production is the only available environment, stop: first obtain a separate
incident-recovery plan/approval for the UNKNOWN and queued work. This PR does not
supply authority to change either record or allow queued jobs to dispatch.

Once isolation and the single test are explicitly approved:

1. Confirm correct code/environment, automatic receipts off, drawer automation off,
   an empty ready POS-80 queue and exactly one authorized synthetic job.
2. Permit one test attempt. Record one physical receipt, unchanged alignment and
   a closed drawer. Do not retry if any outcome is uncertain.
3. Verify local SUBMITTED_TO_PRINTER with null spool ID is acknowledged, and server
   AGENT_SUBMITTED_TO_PRINTER/physical_output_verified=false. There must be no
   driver_submission TypeError or second StartDoc for that job.
4. Under the approved test procedure, reconnect/restart and confirm no duplicate
   dispatch/paper. Record physical confirmation only through the separately
   authorized review action. No customer checkout or financial posting is needed.

## Rollback

After rollback approval and safe stop/queue inspection, restore the previous
reviewed connector package (PR #191 baseline) in the same path and identity.
Preserve journals, credentials, server jobs and all audit records. There is no
schema or Railway-variable rollback. The baseline still contains this None-return
bug; rollback is not permission to attempt another print or replay uncertainty.
Existing successful journal entries remain fences readable by the previous version.

## Recorded validation results

- Before fix: the None-return focused test failed on unchanged baseline with the
  exact TypeError and driver_submission diagnostic shown above.
- After fix: **7 focused tests passed**.
- Combined regression run: **204 passed**, no failures or skips. This includes the
  new tests plus diagnostic/redaction/crash recovery, Windows agent/GDI, encrypted
  connector, TLS/proxy, hardware integration and finalized/provisional receipt suites.
- Syntax and `git diff --check` passed. Existing dependency deprecation warnings
  remain. No PostgreSQL test or production migration was needed: all backend code,
  SQL transitions and schema are unchanged. Actual Windows printing was not run.
