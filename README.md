# PixiePinks Ledger (MVP)

## CRM and customer-service workspace

The authenticated `/crm` area is an operational workspace built into the existing
FastAPI/Jinja application. Its responsive sidebar keeps CRM, customer, inventory,
supplier, journal, chart-of-accounts, and financial-report routes in one application.
The dashboard derives unread WhatsApp, human handover, payment-required, lead,
follow-up, and sales-intent metrics from persisted records. Action-required cards link
directly to the applicable inbox filters or CRM records. `/crm/orders` presents
WhatsApp sales intents explicitly as intents rather than completed Shopify orders.

### WhatsApp Inbox

`/crm/whatsapp` is an authenticated, responsive three-panel staff inbox. It includes
newest-first conversations, name/phone search, All/Unread/AI/Human/Payment Required
filters, chronological message history, customer and delivery details, persisted
product images and pricing, and order totals. The browser polls the lightweight list
and selected-conversation APIs every seven and four seconds respectively; Redis,
Celery, WebSockets, and a separate frontend are intentionally not used.

Inbound customer messages increment the conversation's persisted unread count. AI and
staff replies never clear it; only opening that individual conversation does. New
conversations default to **AI**. **Take Over** changes the persisted mode to **HUMAN**
and records the authenticated username. In HUMAN mode the webhook continues signature
verification, idempotency, persistence, timestamps, and unread updates, but it does not
invoke OpenAI, Shopify replies, or the guided sales flow. **Return to AI** resumes
automation only for a future inbound message and does not replay history. A customer's
handover request and `READY_FOR_PAYMENT_HANDOVER` both switch the conversation to HUMAN.

Manual messages reuse the server-side Meta sender. A browser-generated idempotency key
is uniquely persisted to prevent double sends; only a successful Meta result is written
to conversation history with `response_kind=staff`, the Meta message ID when returned,
send status, and authenticated username. Meta failures are shown to staff and are not
represented as successfully sent messages. API responses never include Meta IDs,
Shopify IDs, access tokens, or other credentials.

Staff can also record real voice notes from the WhatsApp composer. The browser uses
`MediaRecorder`, always presents a local playback preview, and sends only after explicit
confirmation. The authenticated backend validates the recording, converts browser WebM,
OGG, MP4, MP3, or WAV input in memory to mono 48 kHz OGG/Opus with PyAV, uploads it to
the existing Meta Media API, and sends it as a WhatsApp voice message. No speech-to-text,
AI voice, or audio binary is stored in PostgreSQL. Conversation history stores only the
Meta media identifier, MIME type, duration, staff identity, outbound message identifier,
and delivery status. Playback is provided through an authenticated server-side media
proxy, so the Meta access token is never exposed to JavaScript. Recordings are limited
to 16 MB and ten minutes; local previews remain available after a failed upload so staff
can retry.

Database startup continues to use `Base.metadata.create_all`. It adds
`whatsapp_conversations` for mode/unread/timestamps and `whatsapp_manual_sends` for
manual-send idempotency. The existing `whatsapp_conversation_messages` table gains
nullable `send_status` and `sent_by` columns via the backward-compatible startup check.
Voice support also adds nullable `media_id`, `media_mime_type`, and
`media_duration_seconds` columns to that same table. Deployments must install the updated
Python requirements so the binary PyAV wheel and bundled media libraries are available;
no system `ffmpeg` command, new environment variable, or Railway service is required.
Production customer names, phone numbers, addresses, messages, and order data remain
behind the existing signed-session authentication. Shopify remains read-only and the
source of truth; the inbox performs no per-row Shopify requests and does not create
orders, take payments, confirm payments, modify inventory, post ledger entries, or send
broadcasts.

### Railway / Nixpacks PyAV runtime

Python 3.12 deployments pin `av==16.1.0` and require its official manylinux wheel. That
wheel bundles FFmpeg, Opus, x264, and the other codec libraries used by PyAV; the project
must not compile PyAV against whatever FFmpeg happens to exist in the builder image.
`nixpacks.toml` sets `PIP_ONLY_BINARY=av` to enforce the wheel and extends the generated
Nixpacks setup with `gcc-unwrapped` in `nixLibs`, which exposes the standard C++ runtime
needed by the wheel. No manual `LD_LIBRARY_PATH`, system x264 package, or application code
change is required.

After changing this dependency, trigger a **clean** Railway build so an older PyAV 14.x
wheel is not restored from cache. Verify the deployed container before running tests:

```bash
python -c "import av; print(av.__version__); print('PyAV import successful')"
```

For PostgreSQL concurrency checks, set `TEST_POSTGRESQL_URL` only in the staging shell to
the dedicated `pixiepinks_test` database. Never point it at the application `railway`
database or production. Then run the two explicit test nodes documented in the Phase 4A
staging checklist.

## Shopify bicycle collections

Guided recommendations use Shopify collection membership as their authority.
The deterministic map contains the eight verified titles `Size 12/16/20/26"
Boys Bicycles` and their corresponding `Girls Bicycles` titles. Admin GraphQL
looks up the exact configured title and supplies the actual collection ID and
handle at runtime; handles and collection GIDs are never guessed or hardcoded.
The service fails closed if exactly one matching collection cannot be verified.

There is intentionally no 24-inch mapping: the supported age starting points are
2–3 → 12 inches, 4–5 → 16 inches, 6–10 → 20 inches, and 11+ → 26 inches.
Age is only a starting recommendation and is never presented as a fit guarantee.
Collection reads use the existing Shopify `read_products` scope; no write scope
or catalogue mutation is required.

## WhatsApp bicycle sales workflow

A generic bicycle request starts a deterministic boy/girl then age conversation.
Once both are known, the backend resolves the exact gender-and-size collection by
verified Shopify title and live GraphQL ID, returns no more than five available
products, and sends each valid Shopify featured-image URL through WhatsApp. Bicycle
cards alone state **Free Islandwide Delivery**. An outbound Meta message ID is mapped
to its Shopify handle, allowing a customer's reply to an image (for example, “I want
this” or “meka ona”) to resolve and re-query that exact product. Multiple live
variants cause the assistant to request the missing choice rather than guess it.

After an exact bicycle variant is live-verified, the assistant offers—but never
automatically adds—the initial service/greasing. Charges are fixed in code: 12 and
16 inches cost Rs. 2,000; 20 and 26 inches cost Rs. 2,500. Bicycle delivery is zero,
and totals use decimal arithmetic. The persisted `whatsapp_order_intents` state then
collects contact name, the customer-provided address, and two distinct Sri Lankan
phone numbers (`07XXXXXXXX`, `947XXXXXXXX`, or `+947XXXXXXXX`). These personal fields
are handled deterministically rather than sent to OpenAI.

Immediately before payment handover, Shopify is queried again for the exact variant,
availability, and current price. The record moves to `READY_FOR_PAYMENT_HANDOVER` and
the existing team workflow is instructed to send payment details; the AI neither
creates a Shopify order nor supplies bank details. Delivery is estimated from the
actual Asia/Colombo date as the third through fourth working day, skipping Saturdays
and Sundays. Public holidays are not currently included, and the wording explicitly
states that this is not a guarantee.

The order-intent states are `PRODUCT_DISCOVERY`, `AWAITING_BICYCLE_GENDER`,
`AWAITING_BICYCLE_AGE`, `SHOWING_PRODUCTS`, `AWAITING_VARIANT`,
`AWAITING_SERVICE_DECISION`, `COLLECTING_DELIVERY_DETAILS`,
`READY_FOR_PAYMENT_HANDOVER`, `HANDED_TO_TEAM`, and `CANCELLED`. A clear cancellation
only cancels the newest active intent. Starting another product category follows the
general live Shopify route and does not inherit bicycle gender, age, size, or service
constraints.

A tiny double-entry accounting app for your shop. Built with FastAPI + SQLite + Jinja2 + HTMX.

## Features
- LKR currency (configurable)
- Chart of Accounts with sensible defaults
- Journal entries with balanced debits/credits
- Dashboard with MTD KPIs
- Trial Balance, Income Statement, and Balance Sheet reports

## Quick Start

1) Create and activate a virtual environment (optional but recommended):
```
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

2) Install dependencies:
```
pip install -r requirements.txt
```

3) Run the app:
```
uvicorn main:app --reload
```

4) Open http://127.0.0.1:8000 in your browser.

## Basic Accounting Rules
- Sales: debit Cash/Bank or Accounts Receivable; credit Sales Revenue.
- Purchases (inventory): debit Inventory; credit Cash/Bank or Accounts Payable.
- Record COGS when you sell: debit Cost of Goods Sold; credit Inventory.
- Expenses: debit an Expense account; credit Cash/Bank or Accounts Payable.
- Owner draws: debit Owner's Equity (or Drawings); credit Cash/Bank.

You can expand the chart of accounts under /accounts to match your shop.

## WhatsApp AI customer service

Incoming WhatsApp text messages use the OpenAI Responses API for short customer-service
replies. The existing Meta webhook verification and outbound Cloud API configuration are
still required. Configure these additional Railway variables:

```text
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-4.1-mini
```

`OPENAI_MODEL` is optional and defaults to `gpt-4.1-mini`. If the API key is absent or
OpenAI cannot produce a reply, the customer receives a fixed safe fallback instead.
Recent inbound and outbound messages are stored in the existing application database;
the application creates the `whatsapp_conversation_messages` table during startup.

### Live Shopify catalogue

Product questions use Shopify Admin GraphQL as a live, read-only source. Configure these
Railway environment variables:

```text
SHOPIFY_SHOP=my-store-name
SHOPIFY_CLIENT_ID=<server-side client ID>
SHOPIFY_CLIENT_SECRET=<server-side secret>
SHOPIFY_API_VERSION=2026-07
```

`SHOPIFY_SHOP` is only the permanent `myshopify.com` subdomain, not the custom website
URL. For `my-store-name.myshopify.com`, use `SHOPIFY_SHOP=my-store-name`. Keep the Client
Secret server-side. The backend obtains temporary access tokens automatically, caches
them in memory, and renews them before their approximately 24-hour expiry.

The app requires only `read_products`, `read_inventory`, and `read_locations`; it does
not write products or inventory and does not request Shopify write access.

## AI Bot Management Center

Authenticated staff manage the store-wide assistant at `/crm/bot-management`. The
center uses a **Draft → Test → Publish → Live** lifecycle: ordinary saves only update
the draft, the simulator can evaluate draft or live rules without sending WhatsApp
messages or creating operational records, and an explicit `PUBLISH` confirmation
creates an immutable version. Historical versions can only be restored **as a draft**,
so rollback also requires testing and deliberate publication. Administrative saves,
publishes, restores, and read-only Shopify discovery are audited with the signed-in
staff username (the current application has one staff permission level).

The database adds `bot_configurations` (live/draft pointers),
`bot_configuration_versions` (immutable snapshots), `bot_knowledge_entries`
(normalized extensibility for scoped content), and `bot_audit_events`. Startup retains
the existing `Base.metadata.create_all()` convention. The initial published version
preserves bicycle age-to-size rules (2–3/12, 4–5/16, 6–10/20, 11+/26), Decimal service
fees (Rs. 2,000/2,000/2,500/2,500), free islandwide bicycle delivery, and the 3–4
working-day estimate. New decisions use the currently published cached version;
publishing invalidates the short-lived cache immediately. Existing selected products
and order intents are not recomputed, so in-progress purchases remain stable.

Configuration is structured into global response settings, policies, global /
collection / product knowledge, FAQs, guided questions, generic recommendation rules,
and specialized bicycle/service/delivery controls. Scoped retrieval is designed to
apply global knowledge first, then collection, then product guidance; deterministic
rules take precedence. No vector database or executable staff rule language is used.
Collection refresh is read-only, and product-specific references are Shopify handles,
not copied commercial data.

**Authority and safety:** Shopify remains authoritative for titles, variants, SKU,
price, inventory, images, URLs, options, and collection membership. OpenAI may
understand and word responses but is never authoritative for those facts, fees,
totals, order/payment state, or deterministic rules. Locked code guardrails preserve
HUMAN-mode pausing, live Shopify checks, webhook idempotency, privacy, payment
handover, and the ban on invented bank/payment data. Staff content is escaped by
Jinja, inputs are bounded/validated, no staff code is executed, and secrets are never
returned to the browser.

Current limitations: roles are not finer-grained than the existing authenticated
staff account; public holidays are stated but are not automatically calculated; the
generic question/rule schema is versioned for expansion while the production guided
executor currently specializes in bicycles. Railway deployment needs no new secret:
deploy the commit normally, allow startup to create the four tables, sign in, review
the seeded live version, test Draft and Live, then publish only intentional changes.
`SHOPIFY_API_VERSION` is optional and defaults to
`2026-07`. If Shopify configuration or the live service is unavailable, product requests
degrade to a safe customer-facing response without disrupting webhook acknowledgement.

### Private inbound WhatsApp media

Inbound WhatsApp images, stickers, videos, documents, audio, and voice notes are saved
as message metadata immediately. The default `meta` storage mode keeps the Meta media
ID in PostgreSQL and requests a fresh temporary download URL only when the active CRM
conversation or AI image analysis needs the bytes. It does **not** create a permanent
copy on Railway, S3, R2, or a volume. The downloader uses the configured Graph API
version, authenticates server-side, validates an allow-listed MIME type and the
`MEDIA_MAX_BYTES` limit, and never returns Meta's URL or token to the browser. The CRM
uses an authenticated `/crm/whatsapp/api/attachments/{id}` proxy with private/no-store
headers. Expired media retains its caption, timestamp, referral, and conversation
record and asks staff to request a resend.

Configure these Railway variables before accepting production media:

```text
WHATSAPP_MEDIA_STORAGE_MODE=meta
MEDIA_MAX_BYTES=20971520
MEDIA_RETENTION_DAYS=90
OPENAI_VISION_MODEL=gpt-4.1-mini
```

No `MEDIA_STORAGE_BUCKET`, endpoint, region, access key, secret, Railway volume, R2, or
AWS configuration is required in `meta` mode. The previous `s3` mode remains available
for backward compatibility by setting `WHATSAPP_MEDIA_STORAGE_MODE=s3` and retaining
the existing `MEDIA_STORAGE_*` variables; historical rows with a `storage_key` continue
to use their private object. Do not remove those variables until historical objects are
no longer needed.

Existing Meta webhook subscriptions and `whatsapp_business_messaging` access are
sufficient for media retrieval; no new Shopify permission or Meta write permission is
required. `META_APP_SECRET` must remain configured so `X-Hub-Signature-256` requests
are verified. Meta media is temporary: `MEDIA_RETENTION_DAYS` is UI/operations guidance
only and does not promise an exact expiry time. Known expired or unsupported attachments
are not repeatedly requested.

AI-mode product images are analyzed only for visible, non-commercial search attributes.
Those attributes are used to query Shopify, which remains authoritative for matches,
availability, variants, prices, and URLs. Ambiguous results trigger clarification and
unmatched images request a name/link. HUMAN-mode images are stored and shown without
vision processing or an automated reply. Click-to-WhatsApp referral metadata is
allow-listed, retained with the message, and displayed in the Inbox.

To safely check authentication, GraphQL access, and a five-product sample in the
configured environment, run:

```bash
python -c 'from shopify_catalog_service import diagnose_catalog_connectivity; print(diagnose_catalog_connectivity())'
```

The diagnostic uses the unfiltered `products(first: 5)` query and reports only status,
count, titles, product types, vendors, and handles. It never returns or logs credentials
or access tokens.

To verify the real bicycle collection titles and handles from a Railway shell, without
printing credentials, run:

```bash
python -m shopify_catalog_service --bicycle-collections
```

This read-only diagnostic lists only collection titles matching a boys/girls wheel-size
category, their actual Shopify handles, and product counts. Compare that output with
`BICYCLE_COLLECTIONS`; the menu label is not treated as proof of a collection handle.

### Sending Shopify products from the WhatsApp Inbox

Authenticated staff can open the **Product** picker beside the WhatsApp composer, search the live
Shopify Admin catalogue by partial title, SKU, product type, vendor, or tag, select an available
variant, and send its current image, price, and published PixiePinks storefront URL. Search and
final variant verification happen server-side; Shopify credentials never reach the browser.

Product sends use the same Meta Cloud API configuration, manual-send idempotency, provider message
status tracking, and outbound product reply-context mapping as the existing Inbox. Free-form product
messages are rejected when the customer's 24-hour service window has expired. The CRM stores only a
historical product snapshot (references, title, variant, SKU, price/currency, image URL, and storefront
URL), not a duplicate catalogue or image binary. Existing databases receive nullable snapshot columns
through the application's additive startup compatibility check; no destructive migration is required.

## Commercial Sales Orders — Phase 1 backend

Phase 1 adds a local, commercial order foundation without changing the existing WhatsApp sales-intent
flow. It includes versioned Alembic migrations, Decimal-only totals, idempotent draft creation,
draft updates, audited order-state transitions, CSRF-protected JSON mutations, and server-side roles.
It deliberately does **not** create Shopify orders, move inventory, verify payments, or post journals.

All new capabilities default to disabled:

```text
SALES_ORDERS_ENABLED=false
SHOPIFY_ORDER_SYNC_ENABLED=false
SALES_ACCOUNTING_AUTO_POST_ENABLED=false
```

`SALES_ORDERS_ENABLED` gates local draft mutations. The other two flags are reserved for later reviewed
phases and do not enable any behavior in Phase 1. Supported sales roles are `ADMIN`, `SALES_MANAGER`,
`SALES_AGENT`, and least-privilege `STAFF`; existing accounts receive `STAFF`, while the conventional
`admin` account is migrated to `ADMIN`. Browser clients obtain a signed-session CSRF token from
`GET /api/sales-orders/csrf` and supply it as `X-CSRF-Token`; creates also require an
`Idempotency-Key`.

### Migration and staging verification

Back up PostgreSQL first. Restore it to staging, install dependencies, and run:

```bash
alembic upgrade head
alembic current
```

The initial revision is additive, baselines the legacy `create_all` schema, adds `users.role`, and
creates `sales_orders`, `sales_order_lines`, `sales_payments`, `sales_order_status_history`,
`shopify_sync_events`, and `accounting_posting_links`. Run `alembic upgrade head` a second time to
verify it is a no-op. Keep all flags disabled while comparing existing trial balance, income statement,
balance sheet, customers, CRM conversations, and sales intents with the pre-migration baseline. Then
enable only `SALES_ORDERS_ENABLED` in staging and exercise the authenticated draft APIs. Downgrade is
intentionally non-destructive: rollback means disabling flags and deploying the prior application;
do not drop order or audit records.

## Commercial Sales Orders — Phase 2 staff interface

With `SALES_ORDERS_ENABLED=true`, authorized `ADMIN`, `SALES_MANAGER`, and `SALES_AGENT`
staff can open **Create Order** from a selected WhatsApp conversation. The responsive drawer
prefills the conversation name and phone, accepts delivery details, and reuses the authenticated,
read-only Shopify product search to add multiple exact variants. The browser shows an immediate
total preview, but the server retrieves every selected variant from Shopify again and replaces all
client-submitted title, SKU, image, URL, availability, and price values before the existing Decimal
service saves the draft. The existing CSRF, permission, discount-approval, and idempotency controls
remain authoritative.

**Sales Orders** at `/crm/sales-orders` provides authenticated search, status/date filters, sorting,
pagination, and links to order detail and the originating WhatsApp conversation. Draft detail pages
show customer/delivery snapshots, product images and variants, authoritative totals, separate order,
payment, fulfillment and Shopify-sync states, and status history. Drafts can be reopened in the
WhatsApp drawer after a refresh. Shopify order creation, inventory changes, payment verification and
accounting posting remain intentionally unavailable; the other two Phase 1 flags should stay disabled.

Phase 2 migration `20261021_02` only adds nullable `product_image_url` and `product_url` snapshot
columns to `sales_order_lines`. Back up PostgreSQL and run `alembic upgrade head` in staging before
enabling the feature. Verify WhatsApp text/product/voice operations and all accounting reports, then
exercise desktop and mobile order creation. Roll back the application by disabling
`SALES_ORDERS_ENABLED`; the migration deliberately does not delete saved drafts or audit data.

## Commercial Sales Orders — Phase 3 Shopify Draft synchronization

Phase 3 can create **one Shopify Draft Order** from an eligible Ledger sales order. It remains disabled
unless both `SALES_ORDERS_ENABLED=true` and `SHOPIFY_ORDER_SYNC_ENABLED=true`; keep
`SALES_ACCOUNTING_AUTO_POST_ENABLED=false`. Only an authenticated `ADMIN` currently has the
`sync_shopify` permission. Creation and refresh actions are CSRF-protected, require explicit staff
confirmation, and run only on the server.

The integration uses the existing Shopify client-credentials token flow and Admin GraphQL API version
`2026-07`. The Shopify app must be configured and reauthorized with the minimum Phase 3 scopes
`write_draft_orders` and `read_draft_orders`; `read_orders` is required to read the associated Order
reference if a merchant later completes a draft in Shopify. Preserve the verified catalogue scopes
(`read_products`, `read_inventory`, and `read_locations`). Phase 3 does not request fulfillment,
refund, inventory-write, or payment-write permissions. No new Railway secrets are required: retain
`SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`, and `SHOPIFY_API_VERSION`.

Draft creation revalidates every exact variant, availability, and price against Shopify, maps customer
and delivery snapshots, whole-number quantities, discounts, shipping, and LKR currency, and verifies
the stored Ledger line/subtotal/grand-total arithmetic before any remote request. Missing customer or
delivery fields, malformed product/variant GIDs, non-LKR orders, and inconsistent totals are rejected
without creating a synchronization event or calling Shopify. The integration compares
the returned Shopify total **and Shopify-calculated tax** with Ledger. The live variant taxability and
Shopify tax engine are authoritative for the remote draft; the Ledger-approved amounts are never
rewritten. Missing taxable-order address data, inconsistent local taxability, and any total/tax or LKR
rounding mismatch are retained as `RECONCILIATION_REQUIRED`. Staff must inspect the draft in Shopify,
confirm the tax and shipping-tax setup, and resolve or remove the remote draft before an approved local
draft can be synchronized again. A deterministic tag, durable `ShopifySyncEvent` operation key, and
atomic expiring database claim are checked before every write. An expired claim is treated as an
uncertain outcome—not automatically retried—until Shopify is reconciled. Status refresh records a
remotely completed Order reference for staff review but never
changes local payment, fulfillment, sales-order, inventory, or accounting state.

Migration `20261021_03` additively adds the draft name, last-sync timestamp, safe error summary, and
sync-event actor/timestamp. Migration `20261021_04` additively adds nullable worker claim and lease
columns plus indexes; it does not alter historical orders or event outcomes. Before rollout, back up PostgreSQL, restore to staging, run `alembic upgrade
head`, keep both feature flags off, and confirm existing accounting and WhatsApp behavior. Then enable
Sales Orders and Shopify sync in staging, reauthorize the Shopify app, create one controlled draft,
verify its totals and deterministic Ledger tag, test Refresh, and confirm repeated clicks do not create
a second draft. For rollback, disable `SHOPIFY_ORDER_SYNC_ENABLED` and deploy the prior application;
do not delete the additive columns, Shopify references, or audit history.

## Commercial Sales Orders — Phase 4A Shopify order lifecycle

Phase 4A provides one-way, read-only linkage when a merchant completes an existing Shopify Draft
Order. It is disabled by default with `SHOPIFY_ORDER_LIFECYCLE_SYNC_ENABLED=false` and does not alter
Phase 3 draft creation. Configure `SHOPIFY_WEBHOOK_SECRET` from the Shopify app's webhook signing
secret; never expose it to the browser. The existing `read_orders` scope is sufficient for the
supported order read and webhook topic. No order, customer, inventory, payment, fulfillment, refund,
or accounting write scope is introduced.

After deploying migration `20261023_05`, manually register an Admin API `orders/create` JSON webhook
for `https://<staging-host>/webhooks/shopify/orders`, using API version `2026-07`. Registration is not
performed by the application. Webhooks are accepted only when the feature flag is enabled, the
HMAC-SHA256 signature and configured shop domain match, and the delivery ID is new. Matching uses a
previously linked Shopify Order GID or the deterministic Ledger order number stored by Phase 3; it
never uses names, phone numbers, or totals. Ambiguous or conflicting references are retained as
reconciliation warnings.

Authorized administrators can run read-only reconciliation from a linked order's detail page. It
queries the existing Draft Order and links its associated Order, but never completes a draft or
changes Shopify. A confirmed remote Order advances only compatible Ledger draft states through
audited transitions; payment and fulfillment remain unchanged. Conflicting local states require
staff review.

Safe rollout: back up PostgreSQL, restore to an isolated staging database, run `alembic upgrade head`,
and keep the new flag off while verifying startup and Phase 3. Configure a staging webhook and secret,
enable only the lifecycle flag in staging, complete a purpose-built Shopify development-store draft,
then verify one webhook audit row, one linked Order, and unchanged payment, fulfillment, inventory,
and accounting data. Test duplicate delivery and manual reconciliation before production approval.
Rollback by disabling the lifecycle flag and deploying the prior application; retain migration
columns and webhook audit history. Disable or remove the webhook subscription if rolling back for an
extended period.

## Commercial Sales Orders — Phase 4B payment tracking

Phase 4B is disabled by default with `SALES_PAYMENT_TRACKING_ENABLED=false`. When enabled, authorized
sales staff can record pending payment claims for `BANK_TRANSFER`, `CASH`, `COD`, `CARD_PAYMENT`, and
`PAYMENT_LINK`. A claim, customer message, or screenshot is not proof of receipt: only an administrator
with the `verify_payment` permission can verify or reject a pending claim after independently checking
the bank, cash, collection, or provider record. COD remains pending until collection is confirmed.

Verified claims alone contribute to `sales_orders.amount_paid`. The server derives `UNPAID`,
`PAYMENT_VERIFICATION`, `PARTIALLY_PAID`, or `PAID`, rejects duplicate external references and
overpayments, and retains immutable payment audit events. Customer advances, evidence uploads,
refunds, reversals, gateway integrations, and automatic notifications are deliberately deferred.

The order detail page can perform an authorized read-only Shopify financial-status refresh for a
linked Shopify Order. Shopify `PAID` is stored as an observation and reconciliation warning; it never
creates or verifies a Ledger payment. The existing `read_orders` scope is sufficient for recent
orders. No Shopify transaction, capture, refund, fulfillment, or inventory mutation is used.

Migration `20261024_06` additively extends the existing `sales_payments` table, creates
`sales_payment_audit_events`, and adds Shopify financial observation fields. Deploy with payment
tracking off, back up PostgreSQL, run `alembic upgrade head`, verify existing Sales Orders and Phase
4A, then enable the flag only after approval. Keep `SALES_ACCOUNTING_AUTO_POST_ENABLED=false`:
verified receipts are operational records and do not create journal entries, receivables, inventory
movements, or cost-of-goods-sold postings. Roll back by disabling the flag and deploying the prior
application; retain the additive columns and audit records.

## Commercial Sales Orders — Phase 4C Shopify payment synchronization

Phase 4C is disabled by default with `SHOPIFY_PAYMENT_SYNC_ENABLED=false`. It adds an explicit,
ADMIN-only **Check Shopify Payment Sync** preflight followed by a separately confirmed **Approve and
Mark Shopify Paid** action. It is never triggered by recording or verifying a Ledger receipt. Execution
also requires Sales Orders, payment tracking, and Shopify lifecycle synchronization to be enabled; keep
`SALES_ACCOUNTING_AUTO_POST_ENABLED=false`.

The Shopify app must have the existing `read_orders` scope plus `write_orders`, and the installation
must permit `mark_orders_as_paid`. The application checks `currentAppInstallation.accessScopes` before
creating or executing an operation and does not request or install scopes automatically. Release and
authorize the updated Shopify app configuration manually, then verify the installed scopes before
enabling the feature. Phase 4C uses only `orderMarkAsPaid`; partial payments and
`orderCreateManualPayment` are out of scope.

Eligibility is fail-closed: the Ledger order must be confirmed, Phase 4A-linked, fully covered by
verified Phase 4B receipts, have no pending claims, and exactly match Shopify currency, total, received,
and outstanding values. The complete reported Shopify transaction set must be available. Successful or
capturable authorizations, pending gateway activity, `AWAITING_RESPONSE`, successful prior payment
transactions, and unknown transaction states block the action so this workflow cannot capture an
existing card authorization. A five-minute preview expires whenever material Ledger or Shopify state
changes.

Migration `20261024_07` additively creates `shopify_payment_sync_operations` and its append-only audit
table. A unique active Shopify Order key, durable database claim, request idempotency, and committed
worker lease prevent normal double-click and concurrent execution. HTTP timeouts, worker interruption,
or failed readback become `OUTCOME_UNCERTAIN`; they are never automatically retried. The read-only
recovery action may mark an operation synchronized only when Shopify proves exactly one matching
successful transaction. Otherwise it remains `RECONCILIATION_REQUIRED` and blocks another mutation.

Production activation checklist: back up PostgreSQL; deploy with the flag off; run `alembic upgrade
head`; verify existing Phase 4A/4B screens; review Shopify Flow, installed apps, and notification rules
that might react to an order becoming paid; authorize and verify `write_orders`; use isolated mocked
tests and a purpose-built low-value non-production order first; then enable the flag only with explicit
approval. Roll back by disabling `SHOPIFY_PAYMENT_SYNC_ENABLED` and restarting the application. Retain
the operation and audit tables. This feature never modifies Ledger payment verification, posts journals
or receivables, moves inventory, changes fulfillment/customers, or intentionally sends notifications.

## Commercial Sales Orders — Phase 4D fulfillment and delivery

Phase 4D adds an internal, audited fulfillment state machine and delivery records behind
`SALES_FULFILLMENT_ENABLED=false`. The operational dashboard is also protected by this flag;
Shopify writes are independently protected by `SHOPIFY_FULFILLMENT_SYNC_ENABLED=false`, and delivery
tracking remains default-off with `SALES_DELIVERY_TRACKING_ENABLED=false`. Internal dispatch never
triggers a Shopify mutation.

Migration `20261024_08` additively creates fulfillment, item, delivery, event, Shopify-operation, and
webhook-audit tables. The first release creates full-order fulfillment records while retaining item-level
quantities for future partial fulfillment. It never modifies the legacy Sales Order fulfillment summary,
payments, accounting journals, receivables, inventory, or customer communication. Tracking links must
be HTTPS.

Shopify API `2026-07` integration uses the Fulfillment Order model and `fulfillmentCreate`, always with
`notifyCustomer: false`. Install and verify the scope appropriate to each accessible location:
`write_merchant_managed_fulfillment_orders`, `write_assigned_fulfillment_orders`, or
`write_third_party_fulfillment_orders`; Shopify also requires `fulfill_and_ship_orders`. Preflight fails
closed for cancelled Orders, inaccessible or ineligible Fulfillment Orders, missing supported actions,
ambiguous line mapping, insufficient quantities, mixed locations, incomplete pagination, or existing
active operations. Timeouts become `RECONCILIATION_REQUIRED` and are never blindly retried.

Optional `fulfillments/create` and `fulfillments/update` JSON webhooks use
`/webhooks/shopify/fulfillments`, the existing HMAC secret, store check, and delivery-ID deduplication.
They update only the external Shopify status snapshot and never overwrite internal delivery evidence.
Register webhooks manually only after approval.

Rollout: back up PostgreSQL; deploy with all three Phase 4D flags false; run `alembic upgrade head`;
exercise Phase 4A–4C regressions; enable internal fulfillment first; validate Shopify scopes and read-only
preflight next; review Shopify Flow/email/app side effects; then authorize one controlled low-value
fulfillment. Roll back by disabling the flags and deploying the prior release while retaining audit data.

Expired, unused Shopify fulfillment preflights are retired automatically only when an administrator
requests a new preflight with a different idempotency key. Retirement uses a database row lock, retains
the original request snapshot and fingerprint with status `EXPIRED`, and releases only its active-operation
slot. Any operation that was approved, claimed, attempted, synchronized, or requires reconciliation is
never retired or retried by this path. No schema migration is required for this recovery behavior.
