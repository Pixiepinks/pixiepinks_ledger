# PixiePinks Ledger (MVP)

## WhatsApp Media Upgrade — Phase 1

Migration `20261024_09` adds `whatsapp_message_attachments`, linked to an existing
conversation message by a restrictive foreign key. It supports multiple attachments
per message with a unique `(message_id, media_id)` pair. Records contain only the
provider media ID, media type, optional MIME type, filename, caption, SHA-256,
size, duration, and creation timestamp. No attachment bytes, access tokens, or
temporary download URLs are stored. Treat filenames and captions as untrusted text.

This phase adds schema only: existing webhook, voice-note, inbox, AI, and outbound
behavior stays unchanged. There is no media download, upload, processing, backfill,
or external API call. Existing voice metadata remains in its original columns.
Normal startup supports both new databases and existing installations; direct
`alembic upgrade head` also applies the additive migration. Back up an existing
database before upgrading. Roll back application code while retaining the new
table; destructive downgrade is deliberately blocked to preserve metadata.

### Phase 2: incoming media metadata registration

The existing signed Meta `/webhook` now registers incoming image, document, video,
audio, and sticker messages in conversation history and links their metadata to
`whatsapp_message_attachments`. An allowlist accepts media IDs, bounded MIME types,
filenames and captions, SHA-256 values, and optional valid sizes/durations. Unknown
fields (including download URLs, tokens, and binary payloads) are ignored. A media
message with missing or malformed media data receives a history placeholder without
an attachment record; malformed optional fields are ignored.

Media registration commits the delivery receipt, message, attachment, and inbox
update together. Repeated delivery IDs are ignored, including within a batch. A
database registration failure returns HTTP 503 so Meta can retry without a committed
receipt hiding the failure. HUMAN conversations retain their mode and receive no
automated reply; AI conversations retain the existing unsupported-media reply.
Audio keeps its existing voice-message history and legacy playback fields. Captions
are metadata only and never enter the text AI or Shopify matching flow. No new
migration, media download, file storage, vision analysis, or UI change is introduced.

### Phase 3: authenticated inbox media access

Staff can view incoming images and stickers, play videos, and download documents
from the existing inbox message bubbles. Attachment captions, filenames, MIME types,
and available sizes appear alongside the controls. Audio retains its existing player
and playback route. Messages without attachment metadata retain their placeholders.

Attachment access requires the existing staff session and verifies that the
attachment belongs to the requested message and conversation. The browser receives
only an application URL, never a Meta media ID, access token, or temporary download
URL. Each request resolves a fresh provider URL, accepts only HTTPS Meta media hosts
on port 443, refuses redirects, and downloads at most 16 MB in bounded chunks into
memory. Nothing is cached on disk or added to the database. Staff may need to ask
the customer to resend media that Meta has expired or that exceeds this limit.

JPEG, PNG and WebP images/stickers, MP4/3GPP videos, supported audio formats, and
PDF, text/CSV, Word, Excel and PowerPoint documents are allowed. MIME types and
image/video/document signatures are checked; HTML/SVG and unknown formats are
rejected. Documents always download as attachments with sanitized filenames rather
than rendering inside the application. New media responses use private/no-store,
nosniff, and restrictive document headers. Expired, unavailable, oversized and
unsupported media show a safe error and retry control. Video byte ranges are
supported, and inbox polling preserves existing media elements and playback.

No migration, AI vision, media interpretation, permanent server file storage,
production database configuration change, or bot workflow change is introduced.
Meta access still uses the existing `META_WHATSAPP_ACCESS_TOKEN` server binding.
The runtime needs HTTPS access to `graph.facebook.com`, `lookaside.fbsbx.com`, and
Meta's `*.fbcdn.net` media hosts. Unit/integration tests mock Meta; production
credentials and customer media are never used for local validation.

### Phase 4: default-off AI image understanding

In **Bot Management → Global Instructions**, image understanding has its own Draft
settings: enable flag, vision model, byte limit, request timeout, and English/Sinhala
fallbacks. Existing published snapshots without these settings remain **disabled**;
there is no migration or automatic activation. Saving a draft does not change live
WhatsApp. Review/test the draft and explicitly Publish only after staging verification.
Defaults: `gpt-4.1-mini`, 4 MB, 12-second request timeout. Allowed models are
`gpt-4.1-mini`, `gpt-4.1`, `gpt-4o-mini`, and `gpt-4o`; administrators can choose
1024 bytes–8 MB and 5–30 seconds. The independent safety ceiling is 20 megapixels.

Eligible AI-mode image messages use the Phase 3 trusted-host downloader with the
configured tighter byte/deadline limits, then one low-detail OpenAI Responses request.
The image is an in-memory data URL, never a Meta URL. Caption and bounded recent
context form one request; obvious phone/email/address fields are redacted from that
context. Prompt instructions include the published Bot Management guidance and
explicitly treat image/OCR/caption text as untrusted data. Only validated category,
confidence, language, intent and short search hints leave the vision service; raw
OCR and visual observations are not stored or copied into customer replies.
SDK/request logging is suppressed within the image task so signed URLs, images,
captions and credentials cannot appear in request/exception logs.

The existing read-only Shopify search verifies all product facts. Replies explicitly
avoid claiming an exact visual match; up to the published maximum (at most five)
available products are formatted from Shopify. At most two bounded catalogue
searches run: specific hints, then a broad-category alternative if needed. No
free-form AI catalogue claims, prices, stock, links or specifications are accepted.
The existing send-product-images setting also applies. Missing/unavailable media,
provider failures and ambiguous observations produce one short configurable fallback.

Bicycle images first confirm same versus similar when the caption has not done so.
The existing persistent bicycle discovery flow then asks for missing gender/age and
uses the published size rules; age supplied in the caption is retained. Existing
collection resolution, product reply mappings, optional service, delivery-detail
collection and payment handover remain in use. Neither image appearance nor OCR
selects a bicycle size or starts an order. New category requests can exit discovery.
Images received during active checkout skip vision; human requests and sensitive
receipt/document interpretations are handed to the team. HUMAN mode never runs
vision or sends an image reply, including a takeover during processing. Turning the
feature off preserves the existing unsupported-media response and inbox metadata.

Use the existing Meta, OpenAI and Shopify credential bindings; no new secret or
database configuration is needed. OpenAI requires HTTPS access to `api.openai.com`
in addition to the Phase 3 Meta and existing Shopify destinations. No permanent
image storage, vision backfill, new migration, live-data mutation, or changes to
document/video/sticker/audio viewing are introduced.
Vision additionally requires the existing `META_APP_SECRET` webhook-signature binding;
an unsigned local webhook never triggers a paid vision call even if the feature is on.

Cost budget: one vision API call per eligible, unique Meta image delivery, at most
500 output tokens, low image detail and four context messages capped at 500 characters
each. Input token usage depends on image/model/configuration; exact cost and live
accuracy have not been measured. Vision SDK retries are disabled. The existing
in-process background-task lifecycle is retained: a worker interruption can lose an
automatic reply, but not the committed incoming message/attachment. Duplicate Meta
deliveries do not retry vision, and existing attachments are not reprocessed.

### Phase 5A: private Meta advertisement referral capture

Optional incoming `message.referral` metadata is stored in
`whatsapp_message_referrals`, with one unique, restrictive foreign key to its local
conversation-message row. Text and media messages are supported in AI and HUMAN
mode. Each original message retains its own attribution, so a later advertisement
does not overwrite an earlier referral in the same conversation. Meta source IDs,
WhatsApp delivery IDs and Shopify IDs remain separate namespaces.

The capture allowlist is `source_id`, `source_type`, `source_url`, `headline`, `body`,
`media_type`, `image_url`, `video_url`, `thumbnail_url` and private `ctwa_clid`.
`captured_at` is supplied by the database clock, never by the webhook payload.
IDs are strings (255 characters for source ID, 1024 for click attribution); type
fields are bounded to 32 characters. Headlines/body text are limited to 500/4000
characters. Malformed fields are omitted independently; empty or unusable referral
objects do not create empty rows. Unknown fields and whole webhook bodies are not
stored. Identifier/URL overflow is rejected rather than truncated. Text controls
and invalid Unicode are removed. Recognizable credential values are rejected.

URL references must be HTTPS on port 443 with no user-info, whitespace, backslashes
or credential/private-attribution query parameters. Source hosts are Facebook,
Instagram, `fb.me` and `fb.watch`; creative hosts are Facebook, FBSBX, FBCDN and
CDNInstagram domains/subdomains. Unknown hosts are omitted and require explicit
review if Meta introduces another host. URLs are metadata references only: capture
never follows links or downloads advertisement media. Signed CDN references may
expire. No referral information is added to the inbox API/UI or AI prompts in this
phase; `ctwa_clid` is attribution only. Existing signature verification remains in
place; production should retain the configured `META_APP_SECRET` binding.

For supported text/media and other referral-bearing messages, receipt, incoming
history, referral, attachment (when applicable) and inbox updates commit together.
Failed registration returns HTTP 503 without committing a processed receipt, so Meta
can retry. The unique delivery-ID claim serializes concurrent duplicate deliveries,
and the unique message foreign key prevents duplicate referrals. Successful duplicate
deliveries do not alter attribution or repeat AI replies. Receipt-only behavior for
unsupported/empty messages without usable referrals is retained. Referral-bearing
messages without a usable text body get a history placeholder without introducing
an AI request. No advertisement ID is passed as a product selection or reply context.
Registration suppresses private SQL/exception details rather than logging referral
values. AI, image understanding, voice, other media, Shopify and sales dispatch are
unchanged after a successful commit.

Migration `20261024_10` follows `20261024_09` and only creates the new table, unique
constraint and foreign key. It supports both direct Alembic upgrades and existing
create-all-before-migration startup. Back up and verify the target database before
deployment; the application database role needs permission to create the table and
constraints. No live database was changed during implementation. Historical data is
not backfilled, and deliveries already marked processed before this release are not
reprocessed. Destructive downgrade is blocked to retain attribution; any application
rollback must keep revision `20261024_10` in its migration graph so startup can read
the current version. Parent-message deletion is restricted while a referral exists.

Tests use isolated SQLite databases and, when `TEST_POSTGRESQL_URL` points to a
dedicated `pixiepinks_test` database, temporary PostgreSQL schemas that are removed
after validation. Never supply a production database URL for tests.

### Phase 5B: staff-only advertisement referral cards

The WhatsApp Inbox now displays a compact referral card immediately above its
originating customer message. It shows available headline, short description,
Ad/Post label and a validated source link. Source type does not identify Facebook
versus Instagram. Missing fields are hidden; records containing only private or
unusable values produce no card. Multiple advertisements remain attached to their
own message rows rather than overwriting conversation-wide attribution.

The authenticated conversation-detail API returns a `referral` object or `null`
per message. Its base display fields are `headline`, `body`, `source_type` and
`source_url`; Phase 5C adds protected preview fields below. Stored display fields are revalidated on read, description text is
capped at 300 characters, and responses use `Cache-Control: private, no-store`.
The query batches the needed fields without loading `ctwa_clid` or source IDs;
raw creative references are inspected only server-side, never serialized. Referral text is inserted with
DOM `textContent`, never HTML. Source links are checked on both server and browser,
require HTTPS on the allowed Meta source hosts, reject credentials/private query
parameters, and open with noopener/noreferrer and no referrer.

Phase 5B introduced **text-only** cards. Phase 5C below adds protected previews;
stored advertisement image/video/thumbnail URLs still never reach the browser.
Text-only cards remain the fallback when a creative reference is missing or unusable.
Existing customer-image, video, document and audio media controls are unchanged.
Inbox polling includes referral content in reconciliation so cards remain bound
to their original messages while unchanged media elements keep playing.

No database migration, webhook/attribution rewrite, AI context or response change,
Shopify matching change, live configuration change or external media request is
introduced. The existing Phase 5A schema head remains `20261024_10`. Asset versions
are refreshed for the updated inbox JS/CSS.

`tests/test_whatsapp_referral_cards.py` covers API security, privacy, missing and
multiple referrals, invalid stored links and media metadata. Its optional Chromium
test also checks actual DOM injection safety, correct card placement, link handling,
no advertisement-media fetches, polling retention and mobile overflow. To run that
browser check, install Playwright into a development/test environment and provide
`chromium` or `chromium-browser`; no browser dependency is added to production.

### Phase 5C: secure advertisement creative previews

Referral cards can display an image advertisement or the stored thumbnail of a
video advertisement. `video_url` is never fetched and no advertisement video player
is introduced. Images use `image_url`, with a safe thumbnail fallback; video ads
require `thumbnail_url`. Missing/unsafe/expired/unsupported creatives leave the
existing headline, description and source link intact. Failed images are hidden
without broken-image icons. Lazy image elements and their stable application URLs
survive inbox polling without repeated preview requests.

The authenticated preview endpoint verifies conversation, inbound message and
referral association before reading any cache or fetching bytes. The inbox returns
only scoped application `preview_url` and controlled `preview_kind` fields, never
the raw creative URL, token or private click ID. An opaque HMAC revision changes
when the stored creative changes; it is a cache-buster, not authorization. Responses
use private/no-store, nosniff, no-referrer and restrictive document headers.

`referral_preview_service.py` is intentionally separate from the bearer-authenticated
WhatsApp media downloader. It never uses Meta/Shopify/OpenAI credentials. Retrieval
allows HTTPS port 443 on `*.fbcdn.net`, `*.cdninstagram.com` (including their roots)
and exactly `lookaside.fbsbx.com`; general Facebook/Instagram application endpoints
are not accepted. URL credentials, sensitive attribution parameters, malformed DNS
names, fragments and encoded controls are rejected. Every actual fetch resolves
the hostname, rejects non-public/local/special or IPv6 transition destinations, and
pins one validated IP while preserving the original Host and TLS SNI/certificate
verification. Redirects, HTTP compression and ambient proxy/netrc/cookie credentials
are disabled. There is one upstream GET and no automatic retry or token refresh.

Limits per application worker:

- Two concurrent disposable preview processes, with duplicate in-flight requests
  rejected rather than queued indefinitely. Busy requests receive a safe 429 fallback.
- 3 MiB streamed input; JPEG, PNG or WebP MIME/signature plus actual decoding required.
- At most 4096 pixels on either side and 8 million input pixels; first frame only.
- Clean pixel-only PNG output, longest side at most 512 pixels and at most 1 MiB.
  A fresh RGB frame strips source metadata/side data; HTML/SVG/video are rejected.
- Twelve-second hard process deadline, three CPU seconds and 512 MiB virtual-address
  limit per child. HTTP connect/read limits are 3/2 seconds with an 8-second streaming
  deadline. Parent cleanup terminates/kills/reaps timed-out workers.
- Child environment and request logging are cleared/disabled. Bounded byte IPC is
  used instead of unpickling decoder output. Neither images nor signed URLs are logged.
- Private RAM-only LRU scoped to staff and originating message: up to 32 entries and
  8 MiB of normalized bytes, five-minute success TTL and one-minute failure TTL.
  Authorization is checked even for cache hits. No disk, CDN or cross-staff cache.

Runtime requires Linux process/resource-limit support, the existing PyAV wheel,
and DNS plus direct outbound HTTPS to the allowed Meta creative CDN hosts. No new
secret, model, migration, database setting or live Bot Management change is required.
Environment proxies are intentionally not used for this uncredentialed fetch path;
restricted deployments must permit direct egress or retain the text-only fallback.
Stored URLs requiring authentication, expired signatures, unsupported hosts/formats,
busy capacity or process restrictions fall back safely. Reload/revisit the conversation
to retry; negative cache entries expire after one minute. RAM caches reset on restart
and are local to each app worker. Live signed Meta references were not used in tests.

Tests mock DNS and upstream HTTP, exercise actual spawned resource-limited workers,
and cover authorization/association, SSRF and DNS rebinding, no credential forwarding,
MIME/content/dimension/stream limits, expiry/redirects/timeouts, private caching and
concurrency, and real Chromium thumbnail/fallback/polling behavior. Existing customer
media controls, webhook capture, AI/HUMAN behavior, Shopify and sales flows are unchanged.
Schema head remains `20261024_10`.

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

### Phase 2 store-wide management

Phase 2 extends—not replaces—the original configuration center. **Catalog Knowledge**
now contains a searchable Shopify Collection Directory backed by a lightweight local
reference cache. **Refresh Shopify Collections** performs one bounded, read-only
GraphQL discovery, updates the observed title/handle/count and last-refresh time, and
marks references no longer returned inactive. It never writes Shopify data. Each
collection opens a workspace with Overview, Knowledge, Guided Selling, Recommendation
Rules, FAQs, Products, and Test sections. The product browser fetches at most 20 live
members only when opened; product selector search requires two characters and returns
at most 10 results.

Knowledge, policy, and FAQ forms hide irrelevant controls: global content has no
Shopify binding, collection content uses cached titled collections, and product
content uses live Shopify product search. The server independently verifies every
binding. Content stays in the versioned draft until explicit publication, is escaped
when rendered, and carries no authoritative price or stock. Runtime selection helpers
apply enabled, non-archived global information before matching collection and product
information and do not include unrelated scoped content.

The generic guided-selling schema is collection-based. Staff can add ordered English
questions, optional approved Sinhala wording, and `CHOICE`, `NUMBER`, `YES_NO`, or
`TEXT` answers. Rules support only allow-listed conditions (`EQUALS`, `BETWEEN`, and
`CONTAINS`) and actions (`SET_ATTRIBUTE`, `CHOOSE_COLLECTION`, `PRODUCT_FILTER`,
`SET_VALUE`, `ASK_NEXT`, and `NO_MATCH`). There is no expression interpreter,
`eval`, executable action, Shopify mutation, accounting action, payment action, or
arbitrary external call. Target collections are selected from and validated against
the refreshed directory. `bot_guided_flow_states` provides a persisted place to pin a
live conversation to its starting configuration version; an active purchase and its
selected Shopify product/variant remain governed by the existing order-intent record.
The existing bicycle runtime remains the only live generic-flow specialization in this
release, retaining gender isolation, approved age sizes, services, free delivery, and
payment handover. Newly configured non-bicycle flows can be built, reviewed, and
versioned, but require a later controlled runtime rollout before handling WhatsApp.

The safe Test Bot continues to compare Live and Draft without Meta sends or production
records. The current simulator reports mode, configuration version, detected category,
normalized bicycle inputs, and matched age rule. Multi-turn generic test sessions,
business-level arbitrary-version comparison, editing/archiving existing snapshot
entries, and full generic live execution remain planned improvements rather than
pretend controls. Authentication remains the existing staff login because this app has
no trustworthy fine-grained permission model. Audit records cover refresh, draft save,
publish, restore, and question/rule draft changes without credentials or customer data.

#### Production verification checklist

1. Sign in and open **AI Bot Management**.
2. Open **Catalog Knowledge**, select **Refresh Shopify Collections**, and confirm the
   last-refresh time and real collection titles appear.
3. Search for a collection and open **Manage**.
4. Inspect its Overview and read-only Products tab.
5. Add collection knowledge to Draft; verify Live Test does not use it and Draft Test
   does after the applicable simulator/runtime support is present.
6. Edit the bicycle 6–10 age rule in Draft; test Live (still 20 inches), then Draft
   (the edited size), and restore 20 inches if the change was only a test.
7. Review validation, enter the exact `PUBLISH` confirmation, publish, and confirm a
   new immutable version appears.
8. Run a real WhatsApp bicycle enquiry for gender, age, products/images, service,
   delivery details, and payment handover.
9. Open CRM Inbox, confirm history/unread state, use **Take Over**, send a staff reply,
   then **Return to AI** and verify automation only resumes on the next inbound message.
10. Confirm leads, sales intents, accounting pages, Meta signature verification, and
    Shopify live price/availability continue operating normally.

Deployment requires no new secrets and no Shopify write scopes. Deploy the committed
application normally; startup `Base.metadata.create_all()` creates
`shopify_collection_references` and `bot_guided_flow_states`. Then sign in, refresh the
directory, review Draft and Live, run the checklist above, and publish only approved
changes. Existing Railway, Meta, OpenAI, database, and Shopify variables are unchanged.

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

### WhatsApp outbound staff attachments — Phase 6A

The Inbox paperclip offers Photos (JPEG/PNG) and Documents (PDF), with a local
preview, optional caption (1,024 characters), removal and upload/send progress.
Staff can send in AI or HUMAN mode within WhatsApp's 24-hour customer reply
window; the action does not switch modes or invoke AI. Videos/additional Office
formats are a separate Phase 6B change. Existing authenticated attachment
retrieval, timeline rendering and Meta delivery-status updates serve outbound
attachments too. No uploaded files are placed in public static storage.

`WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES` defaults to 4 MiB (hard ceiling 5,000,000
bytes). `WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES` defaults to 8 MiB (hard ceiling
16,000,000 bytes, also below the existing authenticated download cap). Defaults
require no configuration changes. Existing Meta token/phone/API configuration
and messaging permission are reused. Do not expose these credentials to the
browser. Provider support/reply-window errors remain authoritative.

Requests require an existing staff login, an allowed CRM staff role, a session
CSRF token and an idempotency key. The entire multipart request is bounded,
including requests without Content-Length, with a 45-second read deadline and
four active uploads per application worker. Extensions select server-owned MIME
types; browser MIME labels are ignored. Content is parsed in a fresh subprocess
(10-second wall timeout, 5 CPU seconds, 512 MiB address space). Images must decode
as JPEG/PNG and stay within 20 million pixels; PNG CRCs are checked. PDFs must
parse, be unencrypted, and contain at most 1,000 pages. Documents are never
rendered or executed. File bytes exist only in bounded memory/temporary multipart
spools, which are closed after the request. There is no permanent local retention
or antivirus guarantee. Sensitive provider diagnostics and content are not logged.

Migration `20261025_11` adds only `whatsapp_media_sends`, containing unique durable
claims, staff/conversation references, a content fingerprint, state, uploaded Meta
media ID and the successful local message reference. It preserves all existing
history and works with direct Alembic upgrades and ORM-first startup. Retain this
migration in the Alembic graph during application rollback. Destructive downgrade
is intentionally blocked because deleting receipts can permit duplicate sends.

Upload and customer send are separate operations. Meta uploads can be orphaned
when a send fails; no permanent local file is retained or automatic Meta deletion
attempted. Only a real provider message ID creates a successful timeline message.
History, attachment metadata and the success receipt commit together. A committed
SENDING claim precedes any customer send. Network interruption, process restart
or persistence failure after acceptance must **not** blindly resend; duplicate
requests return the durable status. Staff must reconcile uncertain outcomes with
the customer/Meta before starting a fresh attempt. Successful sends and definite
rejections can be checked through the scoped authenticated status endpoint.
Browser session storage contains only a staff/conversation-scoped random operation
key, never bytes or credentials; refresh retrieves status instead of resending.
Switching customers discards an unsubmitted file and is blocked during an active
send. Private/no-store responses apply to policy, receipt and media endpoints.

Automated outbound tests use isolated databases and mocked Meta responses. They
cover actual JPEG/PNG/PDF parsing, spoofed and malformed files, size/dimension
limits, auth/CSRF/scope, captions, provider failures, concurrency, database failures
before/after remote send, delivery statuses and Chromium composer/refresh behavior.
Dedicated PostgreSQL checks require `TEST_POSTGRESQL_URL` pointing to an isolated
`pixiepinks_test` database; they create/drop only disposable schemas. Live Meta
upload/delivery is intentionally not exercised by automated tests.
