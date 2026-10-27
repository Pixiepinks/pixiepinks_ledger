# General Test Bot AI preview

Baseline: verified main `f134eabc6b05cd299165fc9e948882f66af5711f`.

## What changed

The primary **Bot Management → Test Bot** screen now generates an AI preview
instead of returning a fixed bicycle/general-enquiry simulator sentence. It
supports questions about all Shopify retail categories, greetings, delivery,
returns and other customer-service topics. There is no category whitelist or
bicycle-only response branch in the new AI endpoint.

The old HTML form POST remains a deterministic, clearly labelled **Legacy
rule-only trace** for compatibility. It does not call the AI. The new UI uses
the separate authenticated JSON simulator endpoint.

The simulator does not publish settings or execute customer actions. Live generated
AI replies now read published Global Instructions using the same response-preference
prompt as this preview. DRAFT remains preview-only. Fixed workflow, fallback,
text/audio/image messages retain their existing wording; publishing does not
translate those messages. Learning retrieval does not need to be enabled.

## Using the simulator

1. Sign in as ADMIN or SALES_MANAGER and open **Bot Management → Test Bot**.
2. Select **Current DRAFT** to preview saved changes or **Current LIVE** to
   preview published configuration. Unsaved form edits are not included.
3. Enter a synthetic customer question or fill a sample. Filling a preset,
   changing inputs or selecting a configuration never makes a provider request.
4. Optionally describe hypothetical previous context. No actual customer
   conversations are loaded. Descriptions are unverified data, not image vision,
   quoted-message resolution or proof of which advertisement was clicked.
5. Shopify lookup defaults to automatic for product questions using the existing
   product-intent detector and generic catalog search. It can be turned off for
   that test. Supplying an exact product handle explicitly requests read-only
   verification, independently of automatic search. No arbitrary URLs are fetched.
6. Click **Run AI simulation** once. One AI request may incur API charges.
7. Review the proposed reply, provider status/tokens, Shopify matches, warnings
   and simulation trace. Changing inputs clears stale results; it does not run
   another test automatically.

Staff without manager permission can view the page but cannot run a billable
simulation. The API independently enforces authorization and CSRF.

### Trying Singlish

In **Global Instructions**, save a DRAFT preference such as:

> Always reply in friendly, natural Singlish: Sinhala expressed using Latin
> letters, even when the customer writes in English. Do not use Sinhala-script
> characters. Preserve product names, prices, sizes and URLs accurately.

Leave other settings unchanged. Select DRAFT in Test Bot and try an English
question. The selected saved response preferences are supplied explicitly to
the preview model; a specific language instruction takes precedence over the
general follow-customer-language default, while security and verified facts
remain authoritative. There is no post-generation transliteration or guarantee
that a model response is fluent Singlish: inspect the actual output.

No publication is needed to preview DRAFT. Lesson comparisons also receive the
published Global Instructions in **both** comparison arms, so language settings
are held constant when evaluating a learned example. Learning retrieval can stay
off throughout. Only published preferences affect live generated AI replies.

## Configuration and knowledge scope

The simulator reads a copy of the selected `BotConfiguration` JSON, reports its
published version and (for DRAFT) draft revision, validates configuration, and
bounds response preferences. It never saves a draft or initializes configuration
from a preview POST. The existing management page's standard configuration
initialization behavior is unchanged.

Relevant enabled, non-archived policies, FAQs and catalog knowledge are selected
with the existing configuration matcher. Global entries are allowed; product
entries require a server-verified matching handle. Collection entries require
membership read directly from Shopify for returned products. Their references
and applicable verified product handles accompany the context so facts are not
silently detached from their scope. Failed membership lookup excludes scoped
content and produces a warning. It does not broaden the entry to GLOBAL.

At most ten products are returned, further restricted by selected maximum-products
settings. Shopify search results verify those products' data, not the identity
of a product referred to by a generic message, photo or advertisement. Catalog
unavailability is distinguished from a successful search with no match.

Approved bicycle/delivery settings remain available as configuration data.
Custom guided selling/recommendation workflows are not executed by this preview;
it is not an end-to-end webhook, cart, order, payment or fulfillment test. No
customer order/payment/account data is loaded or fabricated. Model replies must
not claim that a handover, media send or business action occurred.

## Isolation, privacy and cost

`POST /crm/bot-management/api/simulate` requires the authenticated manager session,
CSRF token and strict bounded JSON. Arbitrary conversation IDs, custom model or
instruction overrides, live-gate toggles and unknown fields are rejected.
The browser renders output and trace using text nodes and private/no-store
responses. Product links use the trusted storefront and validated handles.

The endpoint uses SELECT queries only. It does not create messages, conversations,
leads, orders, payments, feedback, learning jobs, audits or configuration versions.
It does not change AI/HUMAN mode or conversation timestamps. Existing WhatsApp
send functions are never called. All AI test options are request-local; a
concurrent real reply cannot inherit DRAFT instructions or test context.

Inputs use the existing conservative privacy filter; redaction may shorten or
alter a sample and is reported in the trace. No source learning evidence is
loaded. Existing security instructions and Shopify authority remain in the
model request; business preferences are explicitly lower priority. A synthetic
description is never labelled verified catalog data. Model guardrails are not
an automatic guarantee of correct replies; staff review remains necessary.

With automatic product attachments disabled, one run makes at most one model request using the configured `OPENAI_MODEL`,
with a 300-output-token limit, 12-second model timeout, SDK retries disabled
and Responses `store=false`. Provider retention policies still apply. The UI
does not auto-retry and reports failures without presenting a fallback as a
successful generated answer. Tokens come from provider usage; no billed-cost
estimate is fabricated. Manual previews do not increment learning-worker job
cost/history counters; use provider billing for actual costs.

With automatic product attachments enabled, the simulator previews the shared
requirement-discovery and product-selection service, using up to two model requests
and displaying selected images/prices without sending messages. See
[automatic product attachments](automatic-product-attachments.md) for its distinct
selection, inventory, scope and provider limits.

Limits: 20 admitted attempts per manager per hour per web process, two concurrent
previews per process, 2,000-character questions/context, 200-character handles,
4,000-character saved instructions, up to eight selected knowledge entries and
6,000 total content characters. Oversized matching entries are excluded with a
warning. Collection membership checks inspect at most 20 memberships for each
of up to five verified products. The existing Shopify client timeouts apply.
The browser stops waiting after 60 seconds; provider work may already have
incurred charges. In-memory counters reset on restart and are not a global
billing cap across processes.

## Delivery

No migration, new dependency, live configuration/data change, merge, deployment
or customer message is part of implementation. Tests mock all providers. Real
provider language/answer quality must be checked by an authorized operator after
deployment; automated tests demonstrate correct wiring and isolation, not real
model language fluency.

Changed files: `ai_service.py`, `bot_configuration_service.py`,
`bot_simulation_service.py`, `bot_simulation_routes.py`, `learning_test_service.py`,
`main.py`, `templates/bot_management.html`, `templates/_bot_simulation.html`,
`static/bot_simulation.js`, `static/bot_simulation.css`,
`tests/test_bot_simulation.py`, `tests/test_learning_lesson_test.py`, and this guide.


## Validation results

- Full suite: **1,113 passed, 2 failed, 2 skipped**. The payment-claim and
  Shopify-webhook concurrency failures both reproduced on an unchanged archive
  of baseline `f134eabc6b05cd299165fc9e948882f66af5711f`. No tests were disabled.
- Both SQLite-primary skips (fulfillment preflight concurrency and Shopify
  draft retry claiming) passed separately with PostgreSQL as the primary
  database in isolated schemas.
- **133 focused checks passed**, including real Chromium, generic-category
  catalog searches, DRAFT/LIVE style selection, scoped policies/membership,
  private authenticated APIs, live-reply isolation and read-only PostgreSQL.
  Three final product/scope-attribution and published-language checks also
  passed. These counts overlap the full suite.
- Requirements-only Python **3.12.14** dependency validation, real startup and
  restart on SQLite/PostgreSQL, authenticated Test Bot/assets, disabled-worker
  and read-only diagnostics checks passed. Alembic head stays `20261026_16`.
- Providers were mocked. No live AI/Shopify calls, customer messages, production
  data/configuration changes, merges or deployments were performed.
