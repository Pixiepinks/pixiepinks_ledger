# Shopify collection image messages

This replaces the proposed, unmerged native Meta catalog approach. It starts
from verified main `ef77094634ff064ae0fa7d17c0ed9e26cfe277c4` and does not
include `codex/whatsapp-collection-messages` or its Meta catalog configuration.

## Staff workflow

1. Open a customer conversation, then **Product → Send collection…**.
2. Search Shopify collections and preview one. Only products with available
   variants are listed. Selecting a collection, a checkbox or a variant never
   sends a message.
3. Choose one available variant per selected product. A batch contains 1–20
   products. Up to 20 available products are initially selected; larger
   collections start unselected so staff must explicitly choose a subset.
4. Click **Send selected products**. Each product is sent as an ordinary image
   message with the current reviewed name, variant, price and Shopify link.
   Customers receive separate messages, not a native catalog or carousel.
5. The server sends the queued products in the background. **Close — continue sending**, the close icon, changing chats and closing/locking the browser do not pause an accepted queue. The compact **Product sends** indicator above the conversation list restores progress.
6. **Pause after current product** is a separate server action. An already-started send may finish; remaining products wait for **Resume remaining products**. Sent products are never reset. **New selection** lets staff queue another collection; collections for one customer run in order, while a paused customer does not block other customers.
7. Reopening the picker restores the staff user's latest batch for that
   conversation. **Check progress** reads the existing batch; it sends nothing.
8. **Reset failed products for retry** resets only definitely rejected or
   verification-failed items. It sends nothing until staff explicitly resume.
   Its own idempotency receipt prevents duplicate reset actions.

## Availability and review rules

The collection relationship is read directly from Shopify. There is no product
keyword search substituted for collection membership. Products must be ACTIVE,
published and have a valid trusted Shopify image. Variants must be available for
sale; tracked variants also need a strictly positive integer inventory quantity.
Continue-selling/backorders with zero or negative tracked stock are excluded.
Untracked variants use Shopify sale availability, without claiming a stock count.

The preview expires after ten minutes and is signed for the staff member,
conversation, collection and complete preview snapshot. Creating a batch verifies
the snapshot again. Before **each** product send the server rechecks collection
membership, Shopify availability and the exact variant through the existing
product resolver. Changed names, variants, SKUs, images, prices or links are
skipped for fresh review rather than sent with outdated or unreviewed details.
Unavailable/removed/unpublished products are skipped and counted. The existing
24-hour WhatsApp customer-service window is checked before work and again
immediately before the external send. Nothing reopens or bypasses that window.

The individual-product check is a point-in-time observation, not an inventory
reservation. Shopify stock can change after verification; sending does not
reserve stock, create an order or deduct inventory.

## Reuse, security and persistence

- No Meta product catalog, Business Account/catalog IDs, retailer product
  mappings, carousel templates, extra Shopify integration or new
  dependency is required. Background sending adds migration `20261111_40`. Existing Shopify and WhatsApp Cloud API credentials
  are still needed. No existing catalog connection is changed.
- Single-product sending and collection sending share the existing image
  transport, caption formatter, message metadata and Shopify reply-reference
  persistence. Existing image/text/audio/media controls remain available.
- The image transport has an optional detailed outcome for collection sends;
  its existing boolean/message-ID return behavior remains compatible for other
  callers. Definite non-transient 4xx rejections are FAILED; timeouts, 408/429,
  5xx, exceptions and missing acknowledgements are treated as UNKNOWN.
- Existing staff roles and CSRF validation protect every write. Private,
  no-store APIs enforce the original staff member and conversation scope.
  The browser builds all product and status text with `textContent`.
- The existing `whatsapp_manual_sends` table stores discriminated JSON batch
  receipts plus one uniquely keyed receipt per product. The selection and
  request key bind the actor and conversation. Batch/child creation commits
  atomically before sending; PostgreSQL row locks serialize concurrent batch
  creation and compare-and-set claims prevent concurrent duplicate sends.
- A product transitions QUEUED → VERIFYING → PROCESSING → SENT, FAILED,
  SKIPPED or UNKNOWN. The claim commits before any external work. SENT products
  retain the normal Meta message ID, image, price, product/variant reference and
  outbound product mapping. Existing delivery-status webhooks update read,
  delivered and failed states by that ID.
- Batch status summarizes processing, completion, partial results or blocked
  work. Background jobs permit additional reviewed collections to queue behind the same customer. Legacy unfinished batches must first be queued or reviewed. Different staff users retain separate authenticated actions; queue ordering is per customer.

## Ambiguous outcomes and recovery

Meta and the database cannot share a transaction. A crash while VERIFYING or
PROCESSING, a transport timeout, or a database failure after Meta accepted the
message must not trigger automatic resending. Such a batch is blocked or remains
processing, with its current item preserved for operator reconciliation. The UI
offers Check progress and blocks ordinary reset/retry of uncertain items. Do not
delete receipts, create another batch to resend those products or blindly reset
the database state. A database failure before a durable claim sends nothing and
the original action can be retried safely.

The browser no longer drives product delivery. A background selection and its send intent commit atomically; receipt retries return the same queued job. A dedicated server thread uses the existing verified image-send workflow and durable per-product receipts. Pause is an explicit authenticated server action, not a side effect of closing the dialog. Polling progress never sends a product.

### Deployment and restart

Apply additive Alembic revision **20261111_40** (after 20261111_39), then deploy the updated application. It creates `collection_send_jobs`; no existing batch is automatically activated. No new environment variable, paid service or separate Railway worker service is needed. Normal startup starts the collection worker independently of Saved Flows and Global AI.

Unique batch jobs, conditional five-minute claims and the existing item claim prevent concurrent workers from sending the same product. The worker spaces products by at least 1.1 seconds and processes FIFO per conversation. After restart, queued products continue; a confirmed child committed before a parent-summary failure is retained and only the summary is recovered. Interrupted VERIFYING/PROCESSING or UNKNOWN outcomes block for operator review, including later collections for that customer, rather than risking duplicate sends. Paused queues also preserve order. Do not delete receipts or reset uncertain products.

Current actor activity, Inbox grants, reply permissions and the WhatsApp customer-service window are checked before work and before transport. Losing staff access cancels remaining background work without granting access or blocking a new owner's future queue. Closing the reply window pauses remaining work for review. Permission to view/manage a batch stays with its original staff actor and current conversation visibility; the activity list is private/no-store. Failed-only retry remains explicit and is blocked while a later collection is queued, preventing reordering.

Rollback: pause queues and stop collection workers before restoring previous code; retain the additive table and existing send receipts. The older browser-driven UI must not operate alongside background workers on the same batches. No Global AI, consent, orders, stock, ownership or customer-send configuration is altered by this migration.

## Bounds and operational limits

- 20 products per batch, exactly one selected variant per product. Larger
  selections are rejected, never silently truncated or automatically split.
- First 100 matching collections in discovery; search narrows the result.
- At most 200 products per collection scan, in small five-product pages with
  50 variants per product to bound nested GraphQL query cost. A product with
  additional variant pages or a collection exceeding the scan bound is rejected
  explicitly rather than described as complete.
- Each collection scan has a 30-second deadline checked around existing
  bounded Shopify requests. An in-flight request retains its existing timeout;
  the deadline prevents starting further pages. Shopify throttling fails safely
  and can require staff to retry or choose a smaller collection.
- Four provider operations per web process; excess work gets HTTP 429 without
  claiming/sending another product. The browser spaces products by 1.1 seconds.
  Meta's own messaging limits still apply. A caption over 1,024 characters is
  skipped and reported.
- No images are downloaded or stored by this feature. It sends existing trusted
  Shopify CDN image links using the current Meta image-message implementation.

## Delivery and verification

All tests use mocked Shopify/Meta calls and isolated databases. No real customer
messages, live configuration, production database writes, merge or deployment
are performed. Test-number/device checks remain an operator step after review.

Changed files: `main.py`, `whatsapp_service.py`,
`shopify_collection_service.py`, `shopify_collection_routes.py`,
`templates/whatsapp_inbox.html`, `static/shopify_collections.js`,
`static/shopify_collections.css`, `tests/test_shopify_collection_sends.py`, and
this guide. The previous native-catalog feature branch remains separate.


### Original implementation validation (historical)

- Final complete suite with the isolated PostgreSQL integration URL enabled:
  **1,041 passed, 2 failed, 2 skipped**. Both failures are the existing
  payment-claim and Shopify-webhook concurrency tests; both reproduced on an
  unchanged archive of the verified main SHA. Their intermittent behavior was
  also visible in the earlier full run (only the payment test failed there).
- Both tests skipped by the SQLite-primary full run (fulfillment preflight
  concurrency and Shopify draft retry claiming) passed separately with
  PostgreSQL as the primary database in private schemas.
- **52 focused checks passed**, including Chromium, PostgreSQL concurrent
  creation/send/retry with production session settings, stock/price/window
  checks and existing individual-product regressions. Counts overlap the suite.
- An additional focused Chromium run verified recovery after the server
  committed a failed-only reset but its response was lost; confirmed products
  remained untouched.
- Requirements-only Python **3.12.14**, dependency validation, real HTTP startup
  and restart, authenticated Inbox/assets, disabled learning worker and read-only
  diagnostics passed on SQLite and PostgreSQL. Alembic head remains
  `20261026_16`. No new dependencies or migrations.
- Live Shopify/Meta sending and real device delivery were not tested. No
  customer messages, production configuration or catalog linkage were changed.

GitHub PR inspection through GraphQL and REST returned `Forbidden`, so the
previous native-catalog PR could not be confirmed/closed through the API. A Git
PR-head-ref lookup found no PR pointing to its last pushed commit. The old
branch is excluded from this feature, unmerged in the verified main baseline,
and was not deleted. No merge or deployment was performed.

### Background queue verification (11 November 2026)

- Relevant collection, product-question, mobile layout, assignment and question-engine regressions: **130 passed, 1 skipped**.
- Final collection/background suite after recovery and atomic-enqueue checks: **50 passed, 1 skipped** (overlaps the regression run).
- The skipped PostgreSQL test was run separately against disposable PostgreSQL 16: **1 passed**, including repeatable additive migration, preserved existing messages, concurrent job claims and FIFO collections using production session settings.
- Chromium verifies mobile close/reopen without pausing, restored progress, explicit Pause/Resume and lost retry-reset acknowledgement recovery. Fault tests cover confirmed-child/parent-summary restart recovery, uncertain claims, current permissions/window checks, cross-customer independence and in-flight pause. Syntax and whitespace checks passed.
- Provider calls are mocked; no real customer sends, production setting changes, merge or deployment were performed. No new credential or environment variable is required.
