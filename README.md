# PixiePinks Ledger (MVP)

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
