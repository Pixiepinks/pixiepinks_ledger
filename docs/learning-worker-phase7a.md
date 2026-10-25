# Phase7A — worker zero-processing investigation and operations

Verified GitHub main before editing: `54069b01d94bbb5cf439d21756d0c4163928942f`
(PR76). The backend is FastAPI, SQLAlchemy and PostgreSQL-compatible Alembic, not
Flask. Existing Railway cron/service architecture and production configuration
were not changed. No production database, Railway credentials or connector was
available in this environment; the supplied live job IDs and their exact exclusion
reasons could not be inspected. Repository reproductions must not be represented
as verified diagnoses of those production jobs.

## Confirmed repository defects and policy exclusions

Execution path: POST learning/api/run → enqueue → durable QUEUED job → existing
cron tick → worker_lease → pending-job claim → run_job → source_reason/collect →
analyze → ingest/evaluate → candidate review/publication gates → committed result.
The web and worker import the same settings/database modules; actual Railway
per-service values remain an operator verification step.

1. **Silent exclusions:** `run_job` filtered authorization, evaluation-only and the
   configured quiet period in SQL before observing any source. The default24-hour
   quiet period excludes recently active conversations, including Manual Run Now.
   No source/skip metrics distinguished this from no authorization. A zero-count
   COMPLETED record therefore did not prove insufficient human interaction.
   The quiet policy is preserved, with source decisions and UTC deadlines exposed.
2. **Context eviction:** `collect` loaded eight new rows and four arbitrary prior
   rows. If AI/media traffic occupied those prior rows, an actual earlier customer
   message disappeared from context. A genuine new staff reply then failed the
   required human/customer pairing. The collector now retains one genuine previous
   inbound text inside the same time scope, without enlarging its model window. Non-eligible prefix windows are scanned onward
   within a bounded per-run allowance, so early automated/media traffic does not
   force a valid later reply to wait for another daily job.
3. **Later-source starvation:** old batching consumed the conversation limit on
   authorized sources with no new messages. The first ten could repeatedly prevent
   a later valid source from being visited. Already-current and excluded sources
   no longer consume the extraction quota. A500-source metadata scan ceiling is
   reported explicitly; it is not permission to process outside configured bounds.
4. **Failure accounting:** permanent extraction failures advanced the message cursor
   and incremented conversations_processed. Failed batches now retain their cursor
   for a subsequent explicit/daily retry and count as errors, not successful analyses.
5. **Configuration visibility:** a disabled worker returned DISABLED while web-queued
   jobs could remain queued indefinitely. It now records PAUSED_CONFIG and
   LEARNING_DISABLED on pending jobs, with a configuration mismatch when the safe
   web/worker flag snapshots differ. It never enables flags or authorizes sources.

The current AI/HUMAN mode was not an old worker filter, and remains independent of
historical staff evidence. Existing code correctly maps LearningSource.conversation_id
through WhatsAppConversation.id; source IDs are not conversation IDs. Both are
reported separately. Tests deliberately use different source/conversation IDs.
Existing text and Quick Messages persist response_kind=staff plus a staff username;
product cards use staff_product, voice staff_audio. Sent/delivered/read staff text
is eligible; established legacy staff text with null delivery status retains its
previous treatment. Failed/unconfirmed delivery and unknown origins are excluded.
AI, deterministic/system messages, customer messages and attachments never become
staff evidence. Staff product cards contribute verified-origin context/handles,
not price/stock business truth. Evidence thresholds and publication gates are unchanged. Collector/analyzer version2
permits re-evaluating the currently authorized lookback after this fix, including
previously skipped windows. Evidence counts still use distinct source conversations;
version changes cannot manufacture independent evidence. This may spend extraction
budget on old but still-authorized windows, so inspect budgets before ticking.

## Consent scope — no inferred backdating or automatic eligibility

Existing consent_at records the authorization event; the original implementation
allowed the authorized conversation's history within lookback. It did not encode
an after-consent-only restriction. Phase7A does not silently reinterpret consent_at
as a message boundary, nor infer eligibility from the administrator's task text.

New optional messages_from allows ADMIN to explicitly narrow the message interval
**before analysis**. The effective start is max(lookback cutoff, messages_from).
The UI labels entry as Asia/Colombo and the API requires an explicit timezone,
normalizing to UTC. No value is assigned to existing sources. Missing/null input
preserves an existing boundary; earlier boundaries are rejected. Changing scope
after analyses exist is rejected pending privacy review of existing derivatives.
Withdraw using the existing controls when authorization is no longer valid.
If a production authorization already has a legal after-date not represented in
these records, do not run learning until an administrator has verified/recorded
its scope; this code cannot infer that legal basis.

## Migration and API

Additive migration `20261025_15` adds learning_jobs.diagnostics (JSON, default{})
and learning_sources.messages_from (nullable timestamp). Existing counters, consent
events, source IDs, suggestions, versions and audit history are retained. Direct and
ORM-first upgrade paths are supported. Historical jobs have no retrospective
snapshot: the UI explicitly says diagnostics were unavailable. Current diagnostics
are labelled current state and cannot prove historical skip reasons.

Downgrade refuses to drop consent bounds or historical diagnostics. Roll application
behavior back while retaining the additive schema and revision graph. Deleting the
scope column during rollback could broaden eligibility and is not a safe rollback.

Existing authenticated API prefix `/crm/bot-management/learning/api`:

- GET dashboard/history/jobs/{id}: actual counters, safe summary and latest result.
- GET jobs/{id}/diagnostics: ADMIN/SALES_MANAGER detailed count-only run snapshot.
- GET diagnostics/sources?offset=0: ADMIN read-only current source inspection,
  paginated50 sources; CLI supports `--offset`. No extraction/provider call.
- Existing POST run only queues; existing worker claims it on a later cron tick.
- Existing ADMIN POST sources accepts optional timezone-aware messages_from.

Snapshots contain source/conversation IDs, origin counts, time boundaries, cursor,
analysis state, evaluation threshold and exclusion codes. No customer names, phones,
addresses, message excerpts, consent references, tokens, raw errors or provider URLs
are included. Staff can read summaries; only authorized managers can inspect source
snapshots. Existing CSRF/idempotency and private-cache behavior remain in force.
Historical source snapshots are capped at200 entries/run; aggregate counts include
all evaluated sources up to the scan ceiling. At most eight raw windows per
configured conversation quota are scanned per invocation; WINDOW_SCAN_LIMIT is
reported if reached. Selected sources and eligible text IDs are deduplicated for
counts; excluded-message counts describe observed window/context occurrences. Skip reasons count observed decisions
and may repeat on retries; source evaluations record unique source IDs. Provider
actual token counts and conservative reserved costs retain their existing meaning.

## Operator deployment and production verification

Nothing was deployed or merged by this task. After review and deployment approval:

1. Back up the managed PostgreSQL database. Pause the existing cron during rollout;
   apply `python -m alembic upgrade head` in the correctly configured application
   environment. Verify revision20261025_15. Existing startup also runs migrations.
   Do not point local development or test commands at production.
2. Redeploy the main web service and existing `pixiepinks-ai-learning-worker` from
   the same reviewed release. Keep `/railway-learning.toml`, the existing command
   `python -m learning_worker tick` and15-minute cron. Create no additional worker.
3. Verify both services reference the same Railway PostgreSQL DATABASE_URL and
   strong SECRET_KEY using Railway's secret settings, without copying values into
   reports/logs. Verify approved OPENAI_API_KEY is present on the worker. Safe
   diagnostics reveal presence only, not equality or credential validity.
4. Verify LEARNING_ENABLED and database enabled/privacy acknowledgements on both
   services. Publication/retrieval/autopublish are separate gates; do not enable
   automatic publication for this verification. No setting was changed by this PR.
5. In Inbox Details copy the **actual conversation ID**. In Privacy & Sources check
   the separate source ID, allowed status, withdrawal, evaluation-only reservation,
   recorded authorization and any explicit scope start. Do not auto-authorize or
   backdate either source just to get candidates.
6. From the worker's configured Railway shell run:
   `python -m learning_worker diagnose`
   This reads only; it does not claim jobs, advance cursors, call OpenAI, send
   WhatsApp or change consent. For additional sources use `--offset 50` etc.
   The same current report is available to ADMIN in Privacy & Sources. Inspect the
   origin counts, in-scope/excluded-time counts, quiet_until, cursor, eligible text
   window and actual reasons. Do not paste broader Railway environment dumps.
7. For the authorized owner-controlled test source, create genuine new customer
   and staff text inside its approved scope. Quick replies qualify through the
   existing human text workflow. Media/product context does not replace genuine
   staff/customer text pairing. Wait until the configured quiet_until deadline;
   Manual Run Now deliberately does not bypass it or other privacy restrictions.
8. Run Learning Now. Capture its job UUID and QUEUED status. Allow the existing cron
   up to its next15-minute tick. The page polls metadata briefly; History remains
   authoritative if the job waits longer. Verify worker_last_seen changes and that
   this exact UUID becomes RUNNING then terminal on the same database. A CLI status
   alone cannot prove the web service is connected to the same database.
9. Open History → View Diagnostics. Confirm real selected/processed/message counts,
   tokens, errors and source reasons. QUIET_PERIOD is a policy exclusion;
   NO_NEW_MESSAGES is a cursor result; NO_HUMAN_STAFF_MESSAGES is a window evidence
   result; MESSAGE_ORIGIN_UNKNOWN and privacy exclusions appear in window counts;
   EXTRACTION_FAILED is a real failure, not a successful zero run. Do not equate
   source exclusion with insufficient candidate evidence.
10. When extraction yields a supported reusable lesson, verify its evidence links
    and source count in Suggestions. It remains pending review with publication
    disabled; an empty lesson list is valid and is never manufactured. Fewer than
    the configured independent evidence threshold prevents automatic promotion,
    not candidate extraction. Prices/stock/URLs remain Shopify-authoritative.
11. If the worker reports disabled/mismatch, correct configuration only after
    administrator review, then queue a new job; paused configuration jobs are not
    silently resumed. If no source can legally qualify, the accurate outcome is
    an explained zero, not lowered thresholds or altered dashboard totals.

## Remaining limits

The actual production cause and equality of Railway service secrets/database targets
remain unverified without authorized production access. Old job diagnostics cannot
be reconstructed reliably. The collector still uses bounded text windows and the
existing conservative privacy filter, not image/audio transcription or fine-tuning.
Quiet periods, lookback, consent, held-out reservations, model budgets and manual
review remain required. No real WhatsApp sends, billable AI, live data edits or
production configuration changes were used in verification.

## Validation and changed files

The broader relevant suite passed609 checks without failures or skips (learning,
WhatsApp text/media/referrals/Quick Messages/country/details/replies, Bot Management,
Shopify catalog and sales). After final collector/version refinements,96 focused
checks passed, including29Phase7A cases, existing learning/details behavior and
SQLite/PostgreSQL migration/locking checks. A final29-case worker run passed after
API input/privacy cleanup. These runs overlap and must not be summed as unique tests.
Chromium verified explained-zero Dashboard/History, View Diagnostics, mobile layout,
private-content exclusion and existing review workflows. Direct/ORM-first migrations,
existing job/consent preservation, disabled worker, queue claiming, source-ID mapping,
origin classification, lookback/scope/quiet exclusions, retry/cursor behavior and
later-source starvation have fixture coverage. Requirements-only Python3.12.14
startup/restart, pip check and read-only diagnose CLI passed on SQLite/PostgreSQL.
JavaScript syntax, Python compilation and diff checks passed. The complete repository
suite was not rerun; no new failures were observed in the relevant coverage. External
AI/Meta responses were mocked, and PostgreSQL schemas were isolated disposable tests.

Changed files:

- `learning_worker.py`, `learning_diagnostics.py`, `learning_policy.py`.
- `learning_models.py`, `learning_service.py`, `learning_routes.py`.
- `alembic/versions/20261025_15_learning_diagnostics.py`.
- `static/learning_center.js`, `static/learning_center.css`,
  `templates/learning_center.html`.
- `tests/test_learning_worker_diagnostics.py`, `tests/test_learning_migration.py`.
- `docs/learning-worker-phase7a.md`, `docs/learning-center.md`.

No dependency, production environment or Railway topology change was introduced.
