# PixiePinks Ledger (MVP)

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
not write products or inventory. `SHOPIFY_API_VERSION` is optional and defaults to
`2026-07`. If Shopify configuration or the live service is unavailable, product requests
degrade to a safe customer-facing response without disrupting webhook acknowledgement.

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
