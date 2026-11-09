# Meta Ads sync failure investigation — 9 November 2026

## Confirmed production root cause

The administrator supplied repeated `POST /crm/ad-analytics/sync` **409** responses taking 10–19 ms, then supplied the actual error **SYNC_DISABLED** and dashboard state:

- Account `act_109645107`
- **Sync disabled · Configured · Sync NEVER**
- Last success never, currency unavailable, no reporting coverage

This combination identifies the exact failing gate: **the running web process does not interpret `META_ADS_SYNC_ENABLED` as true**. In the deployed implementation, `request_sync()` checks that flag before constructing any Meta client or scheduling retrieval. It raises `SYNC_DISABLED`, which the route maps to HTTP 409. No Meta permissions/account/Insights request is attempted for those rejected requests. Currency and success timestamps are only populated after a successful retrieval; their absence is expected here.

“Configured” means local account/token/version syntax and presence pass validation. It does **not** verify token scope, token expiry or Meta ad-account authorization. Those checks have not yet been reached.

The supplied account ID normalizes correctly from either `109645107` or `act_109645107`. The existing flag loader reads `os.environ` on each call, not a cached Settings instance. It accepts case-insensitive `true` but the deployed version does not trim whitespace. Thus the exact cause is the runtime disabled gate; the evidence does not establish whether it resulted from unapplied Railway variables, a different service/environment being edited, an older running deployment, whitespace or another non-true value. No production variables or live API were inspected/changed by this investigation.

The earlier preview-image 429/410 responses are from `/crm/whatsapp/.../referrals/.../preview`. They do not cause this 409 and are not Ads Insights rate-limit evidence.

## Recommended operational correction

1. In Railway, select the **production environment → pixiepinks_ledger web service → Variables**. Verify `META_ADS_SYNC_ENABLED` is exactly `true`, without quotation marks or surrounding whitespace. Do not change WhatsApp or Global AI settings.
2. Apply any pending Railway variable changes and redeploy that **web service**. Editing only the separate cron service will not enable the web endpoint; environment variables in an already running process do not change when a Railway variable draft is edited elsewhere.
3. Reload Ad Analytics. Verify **Sync enabled · Configured** before clicking Sync Now again. If it still says Sync disabled, the active service/deployment has not received the intended value. The new diagnostics panel exposes the interpreted flag, configuration errors and safe deployment/revision markers from that running process.
4. Click Sync Now yourself after confirming the flag. This is the first stage at which Meta token/account permissions may be tested. Inspect any new error independently; do not assume enabling the flag establishes Ads authorization.
5. For scheduled sync, verify corresponding Ads variables on the existing cron service separately. This PR does not alter any Railway environment variables or activate synchronization automatically.

No token rotation or ad-account change is justified by the current `SYNC_DISABLED` evidence alone. Keep the token private.

## Original HTTP 409 conditions

The deployed route catches only AdsError from `request_sync()`:

| Original detail | Condition | Meta requests |
| --- | --- | --- |
| `SYNC_DISABLED` | `os.getenv('META_ADS_SYNC_ENABLED','').lower() != 'true'` | None |
| `NOT_CONFIGURED` | Missing/invalid account ID, missing token, or invalid Graph version | None |

Administrator/CSRF failures are 403, database failures use the existing private 503 handler, and an active run returns 200/RUNNING. Meta permission/authentication errors occur in background work after a 200/QUEUED response; they cannot explain these original 409s.

## Code improvements in this PR

- Trim outer whitespace consistently for the flag, account, token and Graph version; keep the flag disabled unless its normalized value is explicitly true. Quoted/invalid flags do not silently enable it. Configuration is read dynamically from the current process.
- Replace ambiguous preflight `NOT_CONFIGURED` with fixed per-variable codes: `MISSING_ACCOUNT_ID`, `INVALID_ACCOUNT_ID`, `MISSING_ACCESS_TOKEN`, `INVALID_ACCESS_TOKEN_FORMAT`, `INVALID_GRAPH_VERSION`, `INVALID_LOOKBACK_DAYS`, `INVALID_SYNC_ENABLED`. A false/unset flag still produces `SYNC_DISABLED`.
- Return a safe fixed hint, stage PREFLIGHT and zero API-request count with a 409. Record rejected manual attempts for the valid account without destroying last successful data. A disabled scheduled tick continues to leave the database untouched.
- Add administrator-only GET `/crm/ad-analytics/diagnostics`. It performs **no Meta requests**. It reports interpreted enablement, valid normalized account/version, configuration error codes, token presence as a boolean, safe deployment/revision markers, current lease, timestamps and stored evidence. It never returns a token, token length/hash, raw invalid configuration values, headers, provider error text or request URLs.
- Add fixed-stage, allowlisted numeric request evidence: request count, HTTP status, Meta error code/subcode, published rows. Safe stage events are logged; exception strings and response messages are not. The persisted evidence describes completed/rejected attempts; in-flight request progress is in the sanitized logs. Historic runs without this evidence report “unknown,” not a fabricated zero.
- Preserve successful cache on authentication, permission, validation and publication failures. The successful real-client contract is exercised with mocked HTTP, through publication and report aggregation.
- **Independent reproducible lock defect:** `RUNNING` with `started_at=NULL` could never satisfy the old stale-claim update and remained BUSY forever. Reclaim NULL and expired timestamps; continue protecting active leases and PostgreSQL advisory locks. There is no evidence that this defect caused the confirmed production 409.
- Mark an accepted manual attempt QUEUED so it can be distinguished from an older terminal result.
- Poll the private diagnostics endpoint after Sync Now, then refresh the report after success/failure. Previously the page retained its initial “Last success never” display until a manual refresh even when background work completed. Polling does not repeatedly rerender/request creative previews. It is bounded to six minutes and performs no extra Meta retrievals.

## Migration and rollout

Revision `20261109_36` follows `20261109_35`. It adds nullable JSON `meta_ads_sync.diagnostics` only, with an idempotent column check. Existing reporting facts, sync success history, CRM records, payments, consent and AI settings remain intact.

Apply `alembic upgrade head` using the usual reviewed deployment process before running the updated app. This PR does not merge or deploy automatically. The operational flag correction can be made without this PR if the active service receives exactly `true`; the PR improves normalization, observability, stale-state recovery and UI feedback.

Rollback: deploy the prior app revision; leave the additive diagnostics column in place (downgrade is deliberately non-destructive). No order/payment/customer data restoration is needed. Changing production sync enablement remains an administrator action, outside this investigation.

## Affected files

- `meta_ads_client.py`: normalized live-process configuration and safe request diagnostics.
- `meta_ads_worker.py`: specific preflight rejection, evidence persistence, queued state and stale-claim repair.
- `meta_ads_diagnostics.py`: allowlisted error hints, stages, numeric evidence and safe logs.
- `meta_ads_routes.py`: precise 409s, admin-only read-only diagnostics and safe report evidence.
- `meta_ads_models.py`, `alembic/versions/20261109_36_meta_ads_diagnostics.py`: additive diagnostics storage.
- `static/meta_ads.js`, `templates/meta_ads.html`: diagnostics panel, cache version and automatic result refresh.
- `tests/test_meta_ads_sync_investigation.py`, `tests/test_meta_ads_sync_investigation_ui.py`: targeted unit/API/browser/migration regressions.
- `docs/meta-ads-sync-investigation.md`, `docs/meta-ads-analytics.md`: investigation evidence and operational guidance.

## Validation boundary

All Meta HTTP responses in tests are mocked. The investigation did not perform live Meta calls, edit production variables, modify advertisements, send WhatsApp messages or change Global AI. The confirmed production diagnosis comes from the administrator's SYNC_DISABLED response and runtime dashboard labels, together with the inspected deployed control flow—not an assumed token or permission problem.

## Test results

- **75 Ads tests passed**: the 35 existing Ads tests plus 40 targeted investigation regressions. Coverage includes every preflight rejection, runtime value rereads/whitespace, the supplied account ID and disabled symptoms, zero-request proof, active/NULL/stale leases, PostgreSQL lock-busy/release paths, numeric provider errors with secret-free logs, successful publication/reporting, failed-publication preservation, admin-only diagnostics, additive migration, and browser failure/success refresh.
- **23 existing CRM dashboard and shared authorization tests passed.**
- Total: **98 passed**. Python syntax, JavaScript syntax and `git diff --check` passed.
- HTTP transport is mocked; database tests use isolated SQLite. PostgreSQL advisory lock branches use fake connections. No real Meta account, production database or production credentials were queried.
