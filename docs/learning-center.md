# AI Learning Center: repository inspection and operational contract

Phase7A updates worker selection, diagnostics and the migration head to `20261025_15`.
Use [the current worker rollout guide](learning-worker-phase7a.md) for those deployment
and verification steps; the inspection/validation baseline below records the original release.

Verified baseline: f5f18b400d791bd7e0f4aa6c51864736480ec797 (PR #74), migration head 20261025_13.

The application is FastAPI/Uvicorn, not Flask. SQLAlchemy 2 uses one Base and
PostgreSQL production/SQLite-compatible migrations. Startup creates missing ORM
tables before Alembic upgrades. WhatsApp signature verification, transactionally
claimed inbound receipts, text/media history, HUMAN mode and staff response_kind
are established. Media binaries are retrieved only through scoped authenticated
services; the learning collector excludes attachments and never downloads them.
Customer/order/Shopify relationships remain operational data, not learning outputs.
Quick Messages, referral cards, quoted context and phone-country rendering are present.

BotConfiguration holds separate draft/live JSON; BotConfigurationVersion and
BotAuditEvent preserve publication history. BotKnowledgeEntry already provides a
normalized knowledge entity and is reused for learned items, with a provenance
version table rather than a second knowledge-item store. Administrator live
snapshots remain unchanged by learning. The existing general OpenAI Responses
pipeline uses a 12-second timeout and 300-output-token cap; learning uses a separate
bounded 30-second extraction request. Deterministic bicycle/catalog/order workflows
remain authoritative. Existing product integration supplies read-only live facts.
Staff roles exist; new review operations use ADMIN/SALES_MANAGER and privacy
settings require ADMIN. Existing permissions are not broadened.

No scheduler, consent/exclusion system, vector store, notification delivery service
or .github CI workflow is present. Procfile runs Uvicorn and nixpacks.toml supplies
native runtime support. A separate scheduled command is therefore required; no
web timer or expensive webhook work is introduced. Notifications remain in-app.

Implementation: disabled operational settings; explicit per-conversation eligibility;
privacy-filtered evidence; durable jobs/analysis windows and budget reservations;
strict extraction; deterministic risk/evaluation gates; review and versioned
publication; bounded learned retrieval beneath administrator/Shopify authority;
privacy withdrawal, retention, feedback, API and Bot Management interface.
Automatic promotion initially supports only code-reviewed communication phrases.
Novel language, unknown semantics and business knowledge always require review.
This is controlled retrieval and publication, not model fine-tuning.

## Data, permissions and API

Additive migration `20261025_14` adds fourteen learning-only tables: settings,
leases, sources, jobs, analyses, candidates, evidence, evaluations, knowledge
versions, audits, action receipts, feedback, reservations and regression cases.
Foreign keys retain provenance. Existing operational rows are not rewritten.
Both direct Alembic upgrades and the application's ORM-first startup are tested.
Take a normal database backup before an operator-authorized migration. Downgrade
intentionally refuses to destroy learning/privacy/version history; application
rollback must retain this migration file and its revision graph.

Open **AI Bot Management → Learning Center**. Authenticated existing staff roles
can read; ADMIN and SALES_MANAGER can review, queue work and record feedback;
only ADMIN controls settings, consent/withdrawal and emergency actions. Writes
require the existing CSRF token plus a unique idempotency key. Learning does not
change HUMAN/AI mode, send WhatsApp messages, edit orders or write Shopify.

API prefix: `/crm/bot-management/learning/api`. Reads: `dashboard`, `settings`,
`candidates`, `candidates/{id}`, `candidates/{id}/evaluations`,
`knowledge/{id}/versions`, `sources`, `regression-cases`, `history`, `jobs/{id}`.
Writes: PATCH `settings`; POST `sources`, `regression-cases`, `run`, `feedback`,
`emergency-pause`, and `candidates/{id}/{action}` where action is edit, approve,
publish, reject, more-evidence or rollback. See the authenticated UI for exact
validated request shapes. Run Now only queues work; it never analyzes in HTTP.
Rendered evidence/text uses text nodes, never trusted HTML.

## Privacy prerequisites and deletion

All sources start excluded. An administrator must first document privacy review,
customer notice/lawful processing basis and AI-provider review, then explicitly
allow each source with a non-personal consent reference. The application records
these decisions; it cannot establish their legal adequacy. Confirm local privacy
obligations, provider retention/region/contract, and access policy before enabling.
Do not enter names, telephone numbers or tokens into consent-reference fields.

The collector examines at most eight new text turns plus four preceding turns,
within a configurable 30-day lookback and after 24 quiet hours by default. It
uses known CRM names, phone numbers, addresses and order/customer identifiers for
local redaction, plus conservative patterns for email, payment details, URLs,
identifiers and instruction attacks. Any turn with a redaction placeholder is
excluded from provider input. Attachments, signed URLs and binary media are never
sent to learning. Only genuine staff text supports communication promotion;
AI replies and customer assertions cannot independently establish a business fact.
No complete conversation, provider prompt or exception body is stored in job logs.

Pattern redaction is conservative, **not a guarantee against every unknown name or
multilingual identifier**. Review sanitized evidence and a small shadow sample
before permitting extraction; retain explicit source eligibility. Default-off and
manual review are essential safeguards, not a claim of certified anonymization.

Withdraw a source to stop future learning and immediately disable knowledge derived
from it. Erase additionally clears stored evidence text/references, candidate and
learned-version content, and held-out question text where no legal hold applies.
This explicit privacy erasure is the documented exception to immutable version
content; hashes, audit events and identifiers remain for accountability. It does
not delete the underlying operational WhatsApp history or retract already-sent
provider requests. Legal holds prevent erasure and retention redaction.

Retention runs with each worker tick even when learning is disabled: evidence30,
candidate180, evaluation90, job-log30 and audit365 days by default, configurable
in the UI. Expired evidence revokes dependent knowledge. Retention redacts payloads
and sensitive details; minimal relational identifiers, status/counter/hash records,
reservation totals and idempotency receipts remain to preserve provenance and
retry correctness. This is **payload retention, not complete row deletion**.
An organization needing physical record removal must approve a separate FK-aware
privacy policy and implementation rather than deleting version history manually.

## Publication, evaluation and runtime behavior

Model output is strict bounded JSON, not an authorization decision. IDs must refer
to the actual sanitized window. Backend risk classification overrides model claims.
Exact and lexical near-duplicate checks and administrator-knowledge/policy conflict
checks run locally. Product handles are checked against current Shopify data;
prices, stock and sensitive instructions are prohibited as learned facts. Commercial
policy/product lessons need manager verification and publication, with live
Shopify and existing approved policies remaining authoritative.

Automatic publication is deliberately narrow: exact reviewed phrases in
`learning_policy.COMMUNICATION`, an explicit allowlist, at least three distinct
eligible staff-supported conversations, confidence threshold, passing checks and
daily cap. Novel paraphrases, new languages/semantics, business rules, conflicting
lessons and prior rejection require review. Confidence alone never authorizes.
Default confidence0.95, minimum evidence3 and auto cap1/day. Evidence count is
calculated by the backend, not accepted from the model.

Managers can reserve never-analyzed sources as evaluation-only and approve bounded
held-out question/expected-behavior cases. Such sources never enter extraction.
Evaluation combines independent deterministic safety/conflict checks and these
held-out cases; these are not a statistical benchmark or proof of semantic quality.
Sinhala, English and mixed text are supported; the initial automatic phrase catalog
is intentionally small. Deduplication uses normalized hashes and lexical similarity,
not an embedding database. Ambiguous semantics remain a review responsibility.

Publication creates immutable versions on the existing normalized BotKnowledgeEntry,
under a serialized settings lock. Rollback disables knowledge or restores an older
version by creating a new checked version, preserving history. It never rewrites
administrator live/draft JSON. Withdrawal and publication serialize on the same lock.

General AI responses may retrieve at most four relevant published lessons, with a
2,000-character context budget. Two background slots, 150ms caller wait and a100ms
PostgreSQL statement timeout bound retrieval. Failure returns to existing behavior.
Product lessons require a current verified product match. Administrator policies and
Shopify facts take precedence; learned content is untrusted example data, not system
instructions. Existing deterministic customer-service/sales paths stay authoritative.
Feedback records version IDs and outcomes without query text. Takeover is not assumed
to mean an AI error. Dashboard comparisons are observational consented samples;
small samples/unknown denominators do not produce fabricated improvement claims.

## Railway setup — operator action, not deployed

Existing Procfile/nixpacks web deployment remains unchanged. Do not add a timer to
Uvicorn. After review and deployment authorization:

1. Back up the database, deploy the migration-capable release with all four gates
   false, and verify revision `20261025_14` plus normal WhatsApp/media/CRM behavior.
2. Create a separate Railway cron service from this same repository and release.
   Select `railway-learning.toml` as that service's custom config path. The worker
   command is `python -m learning_worker tick`; restart policy NEVER. Use the same
   private PostgreSQL DATABASE_URL, application secrets and approved OPENAI_API_KEY.
   Do not expose a public domain or attach persistent storage. Start with one worker
   and bounded service CPU/memory (for example1CPU/512MB; measure before expanding).
3. Set the Railway cron schedule to `*/15 * * * *` (UTC) in that service's settings.
   A short-lived tick checks the stored Colombo schedule. Default02:00 Colombo is
   20:30UTC the previous day. Custom non-quarter-hour times run on the next tick.
   Confirm cron timezone in Railway and inspect a real execution before declaring
   scheduling active. This change has not created or enabled any Railway service.
4. Verify normal tick exit and the dashboard worker timestamp. PostgreSQL advisory
   lock plus fenced lease prevents duplicate workers; unique schedule/model keys,
   window checkpoints and evidence constraints prevent duplicate publication. A
   missed schedule catches up at most seven days, one bounded job per tick.
5. Enable shadow extraction only after privacy approvals and explicit source opt-in;
   inspect evidence, spend and rejected output. Reserve independent held-out sources.
   Then permit manager publication/retrieval. Enable automatic promotion only after
   reviewing results and explicitly selecting safe catalog phrases. Confirm actual
   provider billing and error rates throughout rollout.

Required existing variables: DATABASE_URL, SECRET_KEY, approved OPENAI_API_KEY for
extraction. Four new gates (all default false):

| Gate | Effect |
| --- | --- |
| LEARNING_ENABLED | Allows extraction only with DB enabled and privacy acknowledgements |
| LEARNING_RETRIEVAL_ENABLED | Allows bounded retrieval of already published learned items |
| LEARNING_PUBLICATION_ENABLED | Allows publishing only with DB publication enabled |
| LEARNING_AUTOPUBLISH_ENABLED | Adds permission for the narrow automatic path |

Environment gates must be configured consistently on the web and cron services.
DB flags remain separate deliberate controls; disabling extraction does not itself
remove published knowledge. Use emergency pause/disable or retrieval gate to stop
learned responses immediately. No integration token belongs in the UI.

Defaults: ten conversations/job;100,000 reserved tokens/job;250,000/day;
USD1/job and USD2/day at configurable conservative USD10/million tokens; extraction
model gpt-4.1-mini;30s request timeout;1,536 output tokens. Reservations are committed
before every attempt, including retries, and persist across old-job retries/day
boundaries. Actual reported tokens and conservative reserved cost are separate.
These are application estimates, not a guaranteed provider invoice cap: configure
current conservative model pricing and an independent provider billing limit.
Transient calls retry at most three times per job attempt, with at most three job
attempts; configuration/auth failures pause for explicit recovery. Changing the
analyzer/model version permits reanalysis while evidence uniqueness remains intact.

## Monitoring and operational recovery

Check last successful job, next due time, worker last-seen, actual tokens, reserved
cost, errors, queued/retry/paused states, pending reviews and publication history.
A due time is a calculated schedule, not proof of a deployed scheduler: the API
explicitly reports `schedule_verified_active:false`. Logs use constant safe error
codes; inspect provider credentials in the platform secret manager, never paste
exception payloads or signed URLs into audit fields. Alerts are in-app only; there
is no configured email/Slack delivery integration. API-visible metadata is staff-only.

If jobs stop: confirm database migrations, gate/config/privacy states, worker cron,
provider configuration and budgets; retry with a new Run Now key after correction.
Durable reservations and checkpoints survive interruption; failed SQL transactions
cannot publish partial versions. A worker with an expired/stolen lease cannot commit
new learning. Never clear receipts or reset counters to work around a budget.

Emergency rollback: ADMIN Emergency Pause disables automatic publication. To stop
extraction too, turn off the enabled setting or LEARNING_ENABLED. Use the emergency
learned-knowledge disable option or set LEARNING_RETRIEVAL_ENABLED=false on web
to remove learned context. Roll back a specific lesson via its version history.
For application rollback keep the additive schema and migration graph; do not run
a destructive downgrade or restore a database snapshot over new customer activity.
No automated deployment or live configuration change is part of this PR.

## Manual acceptance checklist

- With gates off, load Inbox, send a test text/media in a sandbox, switch HUMAN/AI,
  inspect products, Quick Messages, referral cards, orders and quoted replies.
- In an isolated environment, verify STAFF cannot write; SALES_MANAGER can review
  but cannot set privacy; ADMIN changes require CSRF and idempotency headers.
- Complete documented privacy acknowledgements; allow fixture conversations and
  reserve separate held-out ones. Queue Run Now and verify HTTP only queues work.
- Run worker tick. Inspect sanitized evidence, refs, confidence, risk, conflicts,
  language and genuine counters; verify personal/hostile turns were excluded.
- Edit a pending lesson, attest verification, approve, publish; verify an immutable
  version and relevant learned retrieval without changing live bot configuration.
- Reject a lesson; run analysis again and confirm it cannot silently auto-publish.
- Verify volatile price/stock and policy conflicts block publication, current Shopify
  remains authoritative, and no knowledge produces customer/order writes.
- Roll back/restore a version. Withdraw/erase a source and test immediate retrieval
  exclusion. Confirm legal holds and retained hash/audit provenance.
- Force a provider timeout and DB rollback, restart worker and verify retry/budget/
  receipt correctness; run two workers and confirm only one owns the lease.
- Test Sinhala/English/mixed examples, mobile review layout, no-JS-error rendering,
  empty states and keyboard/native controls. Verify scheduled run in Railway only
  after explicit deployment authorization and observe real billing before widening.

## Validation and changed files

Verified main remained `f5f18b400d791bd7e0f4aa6c51864736480ec797` after a final
GitHub refresh. Baseline full suite:867passed,2failed,2skipped. Final feature full
suite:916passed,2failed,2skipped, including49new learning checks. Both failures
match unchanged main exactly:

- `tests/test_sales_payments_phase4b.py::test_postgresql_concurrent_payment_claim_is_created_once`
- `tests/test_shopify_order_lifecycle.py::test_postgresql_concurrent_duplicate_delivery_is_processed_once`

The two SQLite-primary skips passed separately in isolated PostgreSQL schemas
(fulfillment expired-operation replacement and Shopify draft retry claiming).
Direct and ORM-first SQLite/PostgreSQL migration, real advisory locking and
concurrent publication passed. Chromium covered review/edit/approve/publication,
rollback/settings, staff permissions, mobile viewport and no JavaScript errors;
a final standalone browser check passed after notification rendering changed.
Requirements-only Python3.12.14 `pip check`, fresh startup/restart on both database
engines, authenticated Learning Center/Inbox/Quick Messages HTTP checks and worker
DISABLED execution passed. JavaScript syntax, Python compilation and diff checks
passed. Upstreams were mocked and databases isolated. Live provider quality,
production billing and actual Railway cron execution were deliberately not tested.

Changed files (24):

- Backend: `learning_analyzer.py`, `learning_models.py`, `learning_policy.py`,
  `learning_routes.py`, `learning_runtime.py`, `learning_schedule.py`,
  `learning_service.py`, `learning_worker.py`, `ai_service.py`, `main.py`,
  `models.py`, `settings.py`.
- Schema: `alembic/versions/20261025_14_learning_center.py`.
- UI: `static/learning_center.css`, `static/learning_center.js`,
  `templates/learning_center.html`, `templates/bot_tabs.html`,
  `templates/bot_management.html`.
- Validation: `tests/test_learning_center.py`, `tests/test_learning_migration.py`,
  `tests/test_whatsapp_reply_migration.py` (explicitly scope the existing revision13
  check; the new head has its own direct/ORM-first tests).
- Operations/documentation: `railway-learning.toml`, `README.md`,
  `docs/learning-center.md`. No new dependencies.
