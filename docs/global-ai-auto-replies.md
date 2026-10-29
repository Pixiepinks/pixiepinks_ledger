# Global AI Auto Replies

Administrators can use **AI Bot Management → Settings → Global AI Auto Replies**.
Uncheck the switch for **AI OFFLINE — HUMAN STAFF ONLY**, or check it for
**AI ONLINE**, type `CONFIRM`, and apply. This is an immediate operational setting;
Save Draft and Publish do not control it. Only the `ADMIN` role can change it.
Staff can read the state in the Inbox header and Bot Management. Open pages
refresh the indicator every ten seconds and when the tab becomes visible.
A browser connection failure displays state unavailable, rather than claiming
that a state change succeeded. Reload Settings before submitting another change.

OFF blocks all automated customer responses, including generated text, scripted
bot/order/handover responses, ad greetings/follow-ups, image replies, unsupported
message acknowledgements, and automatic Shopify product attachments. No automatic
"AI is offline" message is sent. Webhooks still authenticate, deduplicate and
store inbound receipts/media/referrals, and apply delivery-status updates. Manual
staff text, Quick Messages, product cards, attachments, saved/recorded voice notes,
orders and other CRM features remain operational. The switch does not edit any
conversation's AI/HUMAN mode or assignment and does not change learning consent,
extraction, publication or retrieval configuration. Explicit staff simulations
and the learning worker remain independent; they do not send customer replies.

## Database and worker contract

The setting is the single row `global_ai_reply_control.id = 1` in the same
PostgreSQL database as existing bot configuration and `bot_audit_events`.
There is no switch cache, environment flag or redeploy needed for state changes.
Migration `20261029_18` adds the table and nullable
`processed_whatsapp_messages.ai_reply_generation`. Initial state is ON, preserving
normal operation. Existing receipts remain NULL and cannot be replayed by the
new worker. Migration/startup does not reset a previously saved OFF state.

Each receipt records the current generation only if ON at receipt admission.
OFF-period receipts store NULL permanently, including on webhook redelivery.
Database errors deny AI admission while the webhook continues its normal receipt
registration when the rest of the database is available. Missing settings or
admission rows fail closed. The queue creation helper and the text/image/fallback
job entry points both check the persisted receipt, sender and current setting.
Every actual state transition increments the generation. Thus OFF invalidates all
previous jobs, even if ON is selected before a queued or running job reaches its
send step. Re-enabling never schedules a backlog. New inbound messages use the
saved per-conversation mode normally.

The job ContextVar carries only identity, session factory and immutable generation;
it is not the global setting. Model checkpoints read the database before customer
model calls. Internal OpenAI SDK retries are disabled for automatic customer jobs.
An already-running model request cannot be recalled; its output is discarded if
OFF or a generation change occurs before sending.

Immediately around each automated Meta send, a fresh database session takes
`SELECT ... FOR SHARE` on the control row, checks ON and the generation, and holds
the lock through the bounded HTTP request. Concurrent conversations can hold shared
locks. The administrator update uses `FOR UPDATE`, updates the row and appends an
audit event in one transaction. It waits for existing send barriers and prevents
later send barriers from passing with an old generation. Stale administrator forms
receive HTTP 409. Duplicate/no-op changes do not increment the generation or create
misleading audit events. Each real change records actor, UTC timestamp, previous
and new booleans, and generation in the existing audit log. An audit failure rolls
back the state update. SQLite development uses `BEGIN IMMEDIATE` to serialize sends
with changes; PostgreSQL is the production concurrency implementation.

Automated send helpers require a job context; text/image/audio transports recheck
when called from that context. Manual API routes remain outside it. Automatic
product batches also validate durable admission at their own entry point and lock
each individual card send. Ad reply delivery uses the same send barrier. Suppressed
claimed ad/card attempts are marked SKIPPED when possible and are never retried as
new work. An uncertain provider outcome retains the existing no-replay behavior.

Any future delayed/retry customer worker must call the decorated entry point with
its **original persisted inbound message ID and sender**, never obtain a fresh
admission ticket at execution/retry time, and use the guarded automatic send helpers.
Do not use a manual transport to implement a new automatic worker. Current customer
replies use FastAPI background tasks, not a durable AI retry queue; the persisted
admission check also covers delayed invocation/re-execution of these entry points.

## External boundary limitation

OFF is effective when its database transaction commits, and the UI confirms only
after that commit. While the change is waiting for a send lock, the last send that
already acquired the barrier may finish. Meta may deliver a request it already
accepted after OFF is confirmed, or delivery may be uncertain if a process or
connection fails after submission. Such a request cannot be recalled atomically
with a PostgreSQL transaction. No later automated request can pass the new gate
once OFF has committed. Releasing the lock is independent of history persistence;
an already-accepted message may still be written to history after OFF so the audit
trail remains truthful. A partial product batch keeps its already-sent cards and
blocks remaining cards.

## Railway deployment

1. Merge the dedicated change and deploy the same revision to every Railway web
   service/replica and any service that can execute customer reply jobs. Use the
   existing shared PostgreSQL `DATABASE_URL`. No new Railway variables, volumes or
   dependencies are required. Do not disconnect Meta or disable the webhook.
2. The existing application startup runs Alembic. Verify the migration reaches
   `20261029_18`; an explicit migration can use the existing command
   `python -m alembic upgrade head`. Apply once before starting additional replicas
   if your release process performs migrations separately.
3. Drain/stop all older web/worker revisions before relying on OFF. Old code does
   not know about this gate and cannot be made safe by the new database setting.
   During the initial rolling deployment, do not claim global OFF is enforced
   until every sending instance runs this revision. Avoid rolling back to a
   pre-control revision while depending on the switch.
4. Log in as an administrator, open Bot Management → Settings, uncheck the switch,
   type `CONFIRM`, and apply. Verify OFF in the Inbox and Dashboard and check the
   administrator audit event. Saving is immediate; do not Publish for this switch.
5. Using a controlled test customer, confirm a new inbound message is visible but
   gets no automatic response, then send a manual staff reply and attachment.
   Confirm status updates continue. No production messages are sent by migration.
6. When ready, check the switch, type `CONFIRM`, and apply. A new message to an AI
   conversation should follow the existing behavior; HUMAN conversations remain
   HUMAN, and OFF-period/invalidated messages receive no delayed automatic reply.

If the change request fails or times out, reload Settings to read the committed
state; do not assume an ambiguous response means OFF. The switch is durable across
application restarts and instance changes. Back it up with the existing database.

## Verification

Tests cover ON/OFF/re-enable, multiple conversations, old/unknown receipts, queued
text/image/unsupported replies, retries after OFF→ON, in-flight text and ad replies,
product batches, model checkpoints, manual text/Quick Messages/product cards/media/
saved voices while OFF, administrator authorization, CSRF, confirmation, stale forms,
audit rollback, webhook status updates, additive migrations, browser confirmation
and status refresh, and PostgreSQL shared/exclusive locking across separate worker
session factories. Existing text, bicycle, image, ad, language and quote-context
regressions remain part of the focused validation.
