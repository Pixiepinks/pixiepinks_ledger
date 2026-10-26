# Native WhatsApp collection messages

Staff can open **Product → Send collection…**, search Shopify collections,
preview available mapped variants, adjust the selection and explicitly send
one WhatsApp interactive `product_list` message. Previewing, checking a box or
changing a collection never sends anything. Existing single-product sends stay
available. WhatsApp controls the customer's browsable catalog layout; this is
not a carousel template or a custom horizontal carousel.

## Deployment configuration (operator action, not performed by this change)

The catalog highlighted in the supplied Commerce Manager screenshot is
**Shopify Product Catalog, ID `859112582464011`**. A different catalog named
“WhatsApp Product Catalog” also exists. The screenshot establishes the Shopify
catalog's identity, not its connection to the sending WhatsApp account.

Configure the web service with:

- Existing `META_WHATSAPP_ACCESS_TOKEN`, `META_WHATSAPP_PHONE_NUMBER_ID` and
  `META_GRAPH_API_VERSION` (default `v26.0`).
- `META_WHATSAPP_BUSINESS_ACCOUNT_ID`: the WhatsApp Business Account ID
  (WABA), **not** the Business Portfolio/Commerce Account ID.
- `META_WHATSAPP_CATALOG_ID`: the Shopify-synced catalog, after an operator
  confirms it is linked to that WABA. The code checks the WABA's
  `phone_numbers` and `product_catalogs` edges before preview/send.
- `META_WHATSAPP_PRODUCT_MAPPING_JSON`: a JSON object mapping each complete
  Shopify variant GID to its **verified Meta catalog `retailer_id`**. Example:
  `{"gid://shopify/ProductVariant/123456":"actual-retailer-id-from-meta"}`.
  The example is not a real mapping. Compare each product/variant in Shopify
  and Commerce Manager before configuring its mapping. Values must be unique.
  Neither Shopify product IDs, SKUs, ad IDs nor Meta Graph product node IDs
  are silently substituted for retailer IDs. A missing mapping excludes the
  variant and is counted in the preview.

The system-user token must have access to the WABA, phone and selected catalog,
with the applicable WhatsApp messaging, WhatsApp business management and
`catalog_management` permissions. Use Meta's supported account-linking workflow;
do not switch the live catalog blindly, because other commerce flows may use it.
No account linkage, live configuration or production data is changed here.

Missing setup shows an actionable message rather than falling back to guessed
IDs or sending separate images. Catalog reads/sends use the existing Graph
API version, fixed `graph.facebook.com` routes and Authorization headers;
tokens are never query parameters or exposed to the browser. Provider error
bodies are not returned or logged. Meta paging URLs are never followed; only
bounded cursor values are used with the same fixed route. Redirects are disabled.

## Availability and limits

- Live Shopify membership comes from `collection(id).products`, not a keyword
  product search. Only ACTIVE, published products and available-for-sale
  variants are considered. Tracked variants additionally require positive
  inventory; untracked variants use Shopify sale availability.
- Mapped retailer IDs must exist and report `in stock` in the linked Meta
  catalog. Meta policy review/product eligibility can still reject a send.
- The customer sees **Meta catalog prices and images**. The staff preview
  labels its prices as Shopify prices. Keep the Shopify-to-Meta catalog sync
  healthy; this feature does not alter catalog prices or guarantee sync latency.
- One message includes 1–30 unique mapped variants in one section. Collections
  with more than 30 verified variants start unselected and require staff to
  choose at most 30; they are never silently truncated or automatically split.
- Shopify scans at most four pages of 50 products, with 100 variants per
  product. A larger/partial collection or more than 200 available mapped
  variants fails with an explanation; it is not presented as the full collection.
- Meta scans at most eight pages of 250 records per edge. The Meta verification
  stage has a 30-second deadline and individual GET timeouts up to eight seconds.
  Existing bounded Shopify GraphQL timeouts apply to each of its four pages.
  Native sends have a 15-second timeout. Four operations per web process can
  perform collection provider work concurrently; excess work gets HTTP 429.
- A signed preview expires after ten minutes and is bound to the staff user,
  conversation, collection, catalog and complete verified preview snapshot.
  Send re-fetches membership, availability, prices and mappings; changed
  snapshots require a fresh review. The service window is checked before
  verification and again immediately before reserving the send.
- Collection sends obey the existing 24-hour customer-service window. They
  are not approved-template sends and do not reopen that window.

## Persistence, retries and Inbox behavior

No migration or new dependency is required. The existing `whatsapp_manual_sends`
table reserves a globally unique request key, bound to actor, conversation and
request body, **before** contacting Meta. Concurrent same-key requests send at
most once. Replay returns the stored outcome; an unrelated action cannot reuse
the same key. CSRF and existing staff-role checks apply.

After Meta confirms a message ID, one existing conversation-message row stores
a readable collection/title/variant/link summary with `staff_product` context.
The Inbox displays that summary and the existing webhook updates its delivered,
read or failed status by the returned Meta message ID. No individual product is
invented as the identity of an entire collection. AI/HUMAN mode, CRM lead status,
Shopify orders, inventory and payments are not changed by sending.

The database and Meta cannot participate in one atomic transaction. A timeout,
invalid success response, network error, process crash or failure to persist
after sending leaves the receipt PROCESSING. The UI retains the original action
and offers **Check send result**, using its original key/body. It never retries
the external send automatically. If the outcome remains unconfirmed, an operator
must reconcile it; do not clear the receipt or send again with a new key. A
definite non-transient Meta 4xx rejection is recorded FAILED. A database failure
before the durable reservation sends nothing and the same key can be retried.

This phase does not implement carousel templates, customer carts/order webhook
processing, automatic order creation, native referred-product resolution for AI,
or new AI response behavior. Existing staff Create Order remains the supported
order workflow. Real device layout, catalog permissions and a live test-number
send require an operator verification after setup; automated tests mock providers
and never contact customers.

## Verification

`tests/test_whatsapp_collections.py` covers explicit-send behavior, staff roles,
CSRF, conversation/user binding, closed service windows, stock/price/mapping
changes, expiry/tampering, limits, membership, missing configuration, transport
payloads, provider errors, private errors, duplicate/concurrent requests,
rollback/retry and uncertain outcomes. Chromium exercises the mobile picker
without sending on selection, then sends only the selected product. PostgreSQL
concurrency runs when `TEST_POSTGRESQL_URL` points at the isolated
`pixiepinks_test` database; the test creates/drops its own schema.


### Implementation validation

Verified main baseline: `ef77094634ff064ae0fa7d17c0ed9e26cfe277c4`.

- Complete suite with the isolated PostgreSQL integration URL enabled:
  **1,041 passed, 2 failed, 2 skipped**. The payment-claim concurrency and
  Shopify-webhook concurrency failures were both reproduced on an unchanged
  archive of that exact main baseline. No tests were disabled or altered to
  hide either failure.
- The two primary-PostgreSQL-only skips (fulfillment preflight concurrency and
  Shopify draft retry claiming) both passed separately in disposable schemas.
- **38 final collection-specific tests passed**, including actual Chromium,
  PostgreSQL concurrency, rollback/retry and the signed delivery-status webhook.
  This focused count overlaps the complete suite.
- Clean requirements-only Python **3.12.14** dependency validation, real HTTP
  startup and restart on SQLite and PostgreSQL, authenticated Inbox/collection
  endpoints/assets, disabled-worker checks and read-only diagnostics passed.
- No live Meta/Shopify calls or real customer/test-number sends were performed.
  Native rendering and live catalog linkage/permissions require operator testing
  after configuration. No migration, dependency change, merge or deploy.

Changed files: `main.py`, `settings.py`, `whatsapp_collection_routes.py`,
`whatsapp_collection_service.py`, `templates/whatsapp_inbox.html`,
`static/whatsapp_collections.js`, `static/whatsapp_collections.css`,
`tests/test_whatsapp_collections.py` and this guide.
