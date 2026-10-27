# Automatic Shopify product attachments

Baseline: main `56a1974` (published reply-language preferences included).

## Enable after review and deployment

In **AI Bot Management → Global Instructions**:

1. Keep the business's approved language and requirement-discovery instructions.
2. Set **Maximum recommended products** to **10** (supported range 1–10).
3. Check **Send product images** and **Automatically discover requirements and
   attach available products**. Save Draft.
4. Preview with DRAFT in Test Bot. Use a hypothetical previous exchange for
   follow-ups, for example “Customer wants a school bag for a 7-year-old girl.”
5. Publish when the preview is satisfactory. Select LIVE to check published settings.

Existing snapshots default to automatic attachments OFF. Saving a draft does not
enable live sending. Uncheck the automatic option and publish to disable it; the
sender also checks published settings between attachments. No new environment
variables, dependencies or database migrations are needed.

These are ordinary WhatsApp image attachments with captions, matching the existing
staff product attachment format: Shopify image, product/variant name, current price
and storefront link. They do not require a Meta catalog and are not native catalog
carousels or interactive product cards. Ten is an upper limit, not a target to fill.

## Selection and conversation

General text routing uses one structured AI discovery request. The model receives
published preferences, up to eight recent conversation turns and bounded published
policy data. It asks for missing relevant requirements, such as age and boys/girls/
unisex preference for children's products, without repeating known answers. Specific
named products need not go through demographic questions. Age is not inferred from a
name, and suitability must not be invented from colours.

Once sufficient information is available, the server searches Shopify using a short
English category/name query. It inspects up to 30 candidates, filters unavailable
variants before limiting to 20 products and supplies the first 10 eligible variants
per product to a second structured selection request. Variant IDs and product
indices must belong to that exact request's verified candidate list. No model URL,
image, price, inventory value or executable action is accepted. The server enforces
the published maximum, distinct products and exact variant membership.

Only active, published products with safe Shopify images, finite nonnegative prices,
storefront purchase availability and **tracked positive integer inventory** qualify.
Zero/negative stock, continue-selling with zero stock, untracked inventory and
unknown stock are excluded. Fewer suitable products means fewer attachments. Search
is bounded, not an exhaustive inspection of the entire shop. Product metadata must
be adequate for age/size suitability; the model may ask further questions instead.

General automatic recommendations exclude product handles in the last 100 product
attachments stored for the conversation. Existing staff sending remains available
for an explicit resend. Automatic product-card rows are excluded from the eight-turn
AI context window so a batch of ten images does not displace the requirement exchange.

Existing bicycle discovery, exact quoted-product handling, order and human-handover
routes keep priority. Bicycle recommendations can use the new published maximum
and checked attachment transport once their existing discovery is complete. They
retain their deterministic language and fit rules. Image-vision routing is unchanged;
the general simulator is not a complete preview of those older workflows.

## Delivery and recovery

The sender claims an inbound batch through a unique, committed `apb_` record in the
existing `whatsapp_manual_sends` table before attachment I/O. Concurrent workers and
repeated/restarted processing cannot replay the same inbound batch. Child attempts
are committed before provider sends. There is no background retry or text fallback
after an uncertain image response.

Before each send it verifies AI mode, the published switches/maximum, the 24-hour
customer-service window and that a newer inbound message has not superseded the
selection. It re-resolves the exact product/variant from Shopify and rejects changed
price, identity, title, unsafe image or nonpositive/unknown stock. Mode/settings/window
and newer-message checks run again after that lookup. Stock is not reserved by a
recommendation; another sale can still occur after a check.

A successful provider ID is stored with full inbox product metadata and quote mapping.
Known failure stops the batch. Ambiguous provider outcomes are UNKNOWN and stop it.
Database failure after a provider send leaves the durable claim in place and never
triggers a replay. Reconcile uncertain delivery in the inbox/Meta before manually
sending anything again. An interrupted batch is not automatically resumed. The
transport stops starting further cards after 90 seconds; a bounded request already
in flight may finish later. It sends a short introduction, not a claim that all
attachments were successfully delivered.

## Preview and provider limits

When the selected configuration enables both switches, Test Bot uses the same
general discovery/selection service and shows selected images and prices. It never
executes the attachment transport or creates customer/learning/send records. The
stock preview is not a promise that stock will still exist at live delivery.

Each run/turn uses one AI request for clarification or a nonshopping response, and
up to two when searching/selecting products. Each call uses the configured model,
12-second SDK timeout, zero SDK retries, Responses `store=false` and at most 1,200
output tokens. Provider charges can apply even if a later lookup or delivery fails.
Automatic discovery/selection does not consume learned examples; learning settings
are left unchanged. Model failures use the existing
fallback; fixed workflow/error text is not translated by this change.

## Verification

Tests cover requirement context, ten-card limits, unknown selection rejection,
zero/unknown/untracked inventory, filtering before the search limit, no-match results,
safe image/price validation, immediate live follow-up attachments, read-only simulator
image/price previews, fresh-stock/price guards, human takeover, expired windows,
publication changes, newer customer turns, uncertain outcomes, post-provider database
failure and concurrent duplicate batch claims. External providers are mocked.

Validation results:

- Final catalog/configuration/recommendation/simulator checks: 117 passed, one
  environment-gated PostgreSQL preview check skipped there (passed in the full run).
- Learning and published-language regressions: 89 passed, one environment-gated skip.
- Dedicated PostgreSQL automatic-delivery checks: 29 passed, including duplicate
  claims and database failure after provider I/O.
- Chromium verified ten images, prices and links without AI or WhatsApp requests.
- Requirements-only Python 3.12.14 startup/restart, migration head `20261026_16`,
  authenticated pages and disabled-worker diagnostics passed on SQLite/PostgreSQL.
- Full regression run: 1,144 passed, three failed, two skipped. Two failures were
  payment-claim and Shopify-order-event concurrency tests, both reproduced on
  unchanged main `56a1974`. The third was an older learning-outage assertion that
  assumed no published response preferences; it now explicitly isolates that
  unrelated preference reader and passes in the 89-test learning rerun. Both
  primary-PostgreSQL-only skips passed separately using disposable schemas.
