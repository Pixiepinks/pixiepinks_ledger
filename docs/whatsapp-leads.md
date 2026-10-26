# WhatsApp leads and advertisement referral attribution

Verified baseline: `89de921c95e97df71adc0ddff0aa733661ee4ad9` (merged PR78).
The existing CRM Leads page reads saved Lead rows, while WhatsApp Inbox reads
WhatsAppConversation. Consequently an Inbox contact need not appear in CRM Leads.
Meta referral capture and authenticated creative retrieval already exist; this
change reuses them without downloading media in the new listing service.

## Staff workflow

Open **CRM / Leads → WhatsApp leads**. Every stored Inbox conversation is visible
without a bulk import, including future conversations on the next page load. The
view covers all dates, supports50 rows/page, search, referral/status/assignee
filters, and links to the existing Inbox. Saved CRM leads remain in their own tab
with the existing date filters and Add Lead form. Manual creation returns to that
saved-record view. Reload or change filters to refresh the WhatsApp list; there is
no image-download polling loop.

Choose **Organize / referrals**. If no matching legacy record exists, create its
CRM record. If matching records exist, explicitly select one to link. Candidates
use supported phone formatting and the existing order-intent notes' exact
WhatsApp-number marker, not a guessed customer identity. No candidate is silently
linked or merged. Existing name, city, notes, assignment, product interest, status,
follow-up, note/task history and source fields remain intact when linking.

After creating/linking, staff can set status, an existing CRM assignee, next follow-up,
manual product interest and an appended note. Existing full CRM record and Inbox
links remain available. These changes do not send messages, create sales orders,
change AI/HUMAN mode, authorize AI learning or alter live bot settings. Other saved
opportunities/order-generated lead records are not automatically merged or deleted.
A phone match is a suggestion for staff review, not an attribution relationship.

## What the source card establishes

The list shows the **most recently captured referral** as a compact48px thumbnail
and source/ad ID only. Headlines, descriptions, source links and additional details
remain in Organize / referrals, keeping the table rows short. Source filters match actual stored ad/post types; they do not infer Facebook
versus Instagram. An ad filter means an ad referral exists in that conversation,
not that every subsequent message or the customer's first-ever contact came from it.

The dialog retains all referrals,20/page, tied to their originating message ID,
message text excerpt and capture timestamp. Different advertisements remain separate.
IDs are displayed as Meta attribution identifiers, never used as Shopify product IDs
or WhatsApp message IDs. Generic greetings do not populate a guessed product.

Where available, a compact image or video thumbnail uses the existing staff-only
conversation/message/referral-scoped preview endpoint. There is no new retrieval
service, video playback or permanent binary storage. Existing HTTPS host review,
SSRF prevention, redirect rejection, bounded download/image processing and private
cache rules remain authoritative. Raw creative URLs, signed retrieval references
and ctwa_clid never enter the new API/DOM. Only safe source links are clickable;
all text uses escaped Jinja or DOM text nodes. Failed/expired previews disappear
cleanly and retain a useful text card.

**No referral received** means Meta supplied no stored referral for that
conversation. It does not prove organic traffic. Old messages without captured
referral data cannot be retrospectively attributed or have a creative invented.
Partial/unknown referral types remain unclassified. Public ad IDs alone are not
used to fetch an arbitrary creative or infer an advertised Shopify product.

## Backend and migration

New authenticated API prefix `/crm/leads/api/whatsapp`:

- GET root: search/filter/paginated conversations, optional linked lead and latest
  sanitized referral. Origin summaries are bulk-ranked per page, not one network
  call per contact. Browsing does not create Lead rows.
- GET `/{conversation_id}`: current linked CRM data, explicit legacy candidates
  and paginated original-message referral history.
- POST `/{conversation_id}/lead`: create/link/manage with existing staff roles,
  CSRF, bounded fields and transactional actor/path/body-bound idempotency receipts.
  The existing transaction helper is reused; this does not invoke learning.

A PostgreSQL row lock plus unique nullable foreign key prevents concurrent requests
from creating multiple primary CRM records for one conversation. Status/assignment/
follow-up updates and note insertion commit with the action receipt. Unauthorized
roles are rejected, error responses are generic/private, and no tokens or upstream
exception payloads are logged. Existing Saved Leads endpoints retain their behavior;
no unrelated authentication refactor is included.

Migration **20261026_16** adds only `leads.whatsapp_conversation_id` with a foreign
key to actual `whatsapp_conversations.id`, and a unique index. Existing rows start
unlinked; there is no data backfill, phone-derived ID, bulk lead creation, new city
field or location tracking. New lead numbers use `LD-WA-{actual conversation ID}`;
Lead primary IDs remain database-generated. Existing manual numbering is unchanged.
Direct upgrades and the application's ORM-first startup are both tested.

## Deployment and rollback — operator action

Nothing was merged or deployed by this task. After review/authorization:

1. Take a normal managed-database backup and deploy the reviewed web release.
   Existing startup runs Alembic. If applying separately in the correctly configured
   environment, use `python -m alembic upgrade head`; verify revision20261026_16.
2. Verify CRM Leads shows the existing Inbox contact count, paging/search/source
   filters, secure previews or text fallbacks, explicit linking and one created
   CRM record after a repeated request. Verify saved notes/tasks/lead details.
3. Check Inbox text/media/voice/Product/order controls, referrals, handover and
   Learning Center. No new dependency, environment variable, worker, cron or AI
   publication setting is required. Worker architecture is unchanged.
4. Application rollback can retain the nullable column/index. PostgreSQL downgrade
   to20261025_15 is tested and preserves lead/note/task rows, but removes the new
   link values; export those links before intentionally dropping the feature schema.
   SQLite downgrade is deliberately guarded because rebuilding a parent table with
   live note/task foreign keys can invalidate history. Retain the additive schema
   when rolling application behavior back on SQLite. No constraints are disabled.

## Validation

Compact-row follow-up:16 relevant checks passed, including real Chromium assertions
that rows show only the thumbnail/ID, remain compact and retain full dialog history.
The change is display-only and needs no additional migration or backend change.

Original feature validation:

Final feature checks: **20 passed**, including real Chromium create/manage/filter/
referral history/mobile behavior, XSS escaping, real successful image responses and
unavailable-preview fallback. SQLite/PostgreSQL direct/ORM-first upgrades, legacy
lead/note/task preservation, PostgreSQL downgrade and actual concurrent lead creation
passed. Staff roles, CSRF, action idempotency, incorrect conversation/message preview
scope, private-ID exclusion, unsafe links, multiple advertisements, paginated history,
legacy order-lead linking and no guessed product have automated coverage.

A full suite run passed981 tests, with2 failures and2 skips. Exact unchanged-main
full suite passed963 tests with the same2 failures and2 skips:

- `tests/test_sales_payments_phase4b.py::test_postgresql_concurrent_payment_claim_is_created_once`
- `tests/test_shopify_order_lifecycle.py::test_postgresql_concurrent_duplicate_delivery_is_processed_once`

Both SQLite-primary skips passed separately on isolated PostgreSQL schemas
(fulfillment preflight replacement and Shopify draft retry). Two final feature
regressions were added after that full run; all20 focused checks passed afterward.
Runs overlap and should not be summed. Requirements-only Python3.12.14 pip check,
fresh startup/restart, authenticated CRM/new API/Inbox/Learning Center checks and
disabled worker/diagnose smoke checks passed on SQLite/PostgreSQL. JavaScript syntax,
Python compilation and diff checks passed. Upstreams were mocked and test schemas
isolated; no production database, real customer send or paid AI call was used.

## Changed files

- `crm_whatsapp_leads.py`, `main.py`, `models.py`.
- `alembic/versions/20261026_16_whatsapp_crm_leads.py`.
- `templates/crm_leads.html`, `templates/crm_lead_view.html`.
- `static/crm_whatsapp_leads.js`, `static/crm_whatsapp_leads.css`.
- `tests/test_crm_whatsapp_leads.py`, `tests/test_crm_whatsapp_leads_migration.py`,
  `tests/test_learning_migration.py` (new head assertion only).
- `docs/whatsapp-leads.md`.

## Expanded lead statuses

The shared list now supports17 states: New, Replied, Awaiting Customer Reply,
Not Responding, Interested, Future Buyer, Follow-up, Negotiating, Out of Stock,
Payment Pending, Paid, Order Confirmed, Dispatched, Delivered, Lost, Not Interested
and Invalid/Spam. The new options appear in the WhatsApp editor/filter and saved
CRM create/detail/follow-up views. Existing stored status keys are retained.

Future Buyer, Paid and Dispatched remain active for explicitly scheduled follow-ups.
Delivered, Lost, Not Interested and Invalid/Spam are excluded from active reminder
counts/lists. Paid/Dispatched here are CRM labels; actual payments and fulfillment
continue through the existing Sales Order workflows. No accounting entry, payment,
shipment, WhatsApp send or AI/HUMAN change is triggered by choosing a lead status.
No migration or dependency is required.

Expanded-status validation:26 final checks passed, including the eight additions,
create/update/filter/render behavior, closed-state reminders, legacy forms and actual
Chromium Future Buyer/Paid/Dispatched flows. A broader run passed68 checks with one
PostgreSQL-only payment concurrency check skipped because no isolated PostgreSQL
URL was supplied. These runs overlap; the full repository suite was not rerun.
