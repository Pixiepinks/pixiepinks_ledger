# Meta Ads Analytics and Sales Attribution

## Existing architecture and prerequisites

The existing WhatsApp integration uses `settings.META_WHATSAPP_ACCESS_TOKEN` and phone-number ID in `whatsapp_service.py`. This change never reads, overwrites or assumes Ads permissions on that token. Existing message-scoped `whatsapp_message_referrals` contain source type, source ID, headline and preview metadata. They are read in place; no referral/customer history is copied or deleted. `CRMAnalyticsPhone` provides canonical identities. Unresolved cached identities remain unresolved. An uncached order-phone formatting variant uses the same existing pure canonical normalizer without writing an identity record.

`Lead.whatsapp_conversation_id`, `SalesOrder.crm_conversation_id`, `crm_lead_id`, customer ID, currency and explicit SalesPayment verification provide CRM evidence. Shopify sync/financial/refund fields are read as additional exclusion evidence, never used to synthesize payment verification. No accounting, inventory, WhatsApp, consent or AI writes are made.

The repository has an existing Railway learning cron (`railway-learning.toml`, normally every 15 minutes). An optional disabled-by-default Ads tick can share that process independently of whether learning/AI is enabled. The first due tick synchronizes, and another successful automatic refresh is due after 24 hours. Manual Sync Now starts a background read-only refresh in the web process; an interrupted process retains old cache. The next manual or scheduled run can retry. No extra cron service is provisioned.

These findings are from repository inspection. No production database or Meta business assets were accessed in this development environment. No Ads credentials are configured here, and outbound Meta access is not permitted by this environment's network policy. Administrator authorization and live account verification remain deployment setup tasks.

## Administrator authorization setup

1. Open Meta Business Settings for the Business Portfolio that owns the PixiePinks ad account. Confirm the numeric ad account ID and account currency/timezone in Ads Manager.
2. Use a Meta app associated with that portfolio, with Marketing API access. Prefer a dedicated integration system user with access only to the intended ad account's reporting tasks. Grant the app/user the ad account access needed to view performance; do not grant campaign management/budget editing for this feature.
3. Generate a server-side token with **ads_read**, subject to Meta's current app access level, business verification and asset assignment requirements. Access to assets belonging to other businesses may require Advanced Access/app review. A user token can be used if appropriate, but has expiry/reauthorization obligations. Follow Meta's currently supported token lifecycle for your chosen user type. Never assume a token is permanent.
4. The integration checks `GET /me/permissions` for granted `ads_read`, and reads `GET /act_<id>?fields=id,currency,timezone_name,account_status` before fetching Insights. A WhatsApp-only token fails this check. No `ads_management` permission is required or requested. If `me/permissions` is unavailable for the chosen supported token setup, diagnose that authorization with Meta rather than bypassing validation.
5. In **Railway → service → Variables**, enter the separate token as `META_ADS_ACCESS_TOKEN`. Do not paste it in chat, GitHub, screenshots, browser forms or issue reports. Keep existing `META_WHATSAPP_*` variables unchanged. Restrict access to Railway secrets.
6. Set the other variables below on the web service and the existing cron service. Give both access to the same database. Only enable sync after migrations, asset permissions and token configuration have been reviewed.
7. As administrator, open Marketing → Ad Analytics and click Sync Now. Confirm SUCCESS, expected account, currency, timezone and coverage. API errors are fixed safe codes; check token expiry/asset assignment in Meta without sharing tokens. View status in the dashboard after refreshing.

Official references: [Marketing API Insights](https://developers.facebook.com/docs/marketing-api/insights/), [Insights parameters](https://developers.facebook.com/docs/marketing-api/insights/parameters), [Access tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/), [System users](https://developers.facebook.com/docs/marketing-api/business-manager/system-users/), [Permissions](https://developers.facebook.com/docs/permissions/). Review current Meta requirements during setup; API fields and access eligibility can change.

## Environment variables

| Variable | Value / default |
| --- | --- |
| `META_ADS_ACCESS_TOKEN` | Separate secret Ads token; required for sync |
| `META_ADS_ACCOUNT_ID` | Numeric ID, optionally prefixed `act_`; only one authorized account |
| `META_ADS_GRAPH_VERSION` | `v26.0` default; explicitly choose a supported version for your app |
| `META_ADS_SYNC_ENABLED` | `false` unless exactly `true`; gate for both automatic and manual retrieval |
| `META_ADS_LOOKBACK_DAYS` | `30` default, clamped to 1–90; refreshed on every sync |

Integration settings and secrets are configured server-side through Railway, not exposed as editable browser credentials. Changing accounts creates a separate cache/sync namespace. Existing cached account data is retained and is not mixed into the new account's reporting.

## Retrieval, storage and operation

The client only issues GET requests to fixed `https://graph.facebook.com/<version>/...` paths. Authorization uses a header. Pagination uses the returned cursor on the same fixed endpoint, never a returned `next` URL. Timeouts, bounded pages/rows, exponential retry (2^attempt, maximum four attempts) and bounded Retry-After support rate limits and temporary failures. Permission/token failures stop promptly. Provider text, token values and raw HTTP exceptions are never stored or logged by this integration.

Daily Insights are requested independently at campaign, ad set and ad levels with `time_increment=1`, `action_report_time=impression`, and explicit `7d_click`/`1d_view` attribution windows. Stored fields include IDs/names, report dates, currency, spend, impressions, reach, inline link clicks, action metrics and sync timestamps. The messaging metric is the documented action type `onsite_conversion.messaging_conversation_started_7d`; absence means unavailable, not zero. Cost per Meta conversation is spend / available messaging action count. Other returned actions are retained in JSON for later inspection without reinterpretation.

Optional GET `/act_<id>/ads` metadata supplies effective delivery status for ads and their campaign/ad set. This is a status snapshot at sync time, not a historical daily status. If this optional endpoint fails or omits an entity, status is unavailable; successful Insights can still be published.

The database has a unique `(account_id, level, entity_id, report_date)` constraint. A successful fetch replaces the refreshed date range atomically, including removal of previously cached activity that Meta now omits. Failure of any required level/page rolls back the entire refresh and preserves all previous valid facts and last successful sync. A PostgreSQL session advisory lock prevents overlapping workers. A committed, 30-minute expiring status claim with publication fencing also serializes SQLite workers; each HTTP fetch run is bounded to five minutes before another attempt is admitted. RUNNING status is visible while retrieval is in progress. A sync-status row tracks safe errors, coverage and last success. After a crash, no partial new cache is committed. PostgreSQL session locks are released explicitly and by connection cleanup after a crash; stale durable claims can be reclaimed after 30 minutes.

Run `python -m meta_ads_worker` from your **existing** scheduled environment daily, or enable its optional tick in the existing `python -m learning_worker tick` process. Do not replace the web service start command or its Nixpacks configuration. The existing learning cron will continue if Ads sync fails. Ads does not change Global AI or learning enablement.

Recent dates are refreshed to capture delayed corrections. Increase lookback up to 90 only when the authorized account/API supports it. No unrestricted historic backfill is performed. The report accepts up to 366 selected dates but only has cached Meta dates and retained CRM evidence; it displays last refreshed coverage and flags an incomplete selected range explicitly. Missing metrics do not become fabricated zero activity. A success older than 26 hours is flagged stale, and errors are visible independently of age.

## Attribution and financial definitions

- **Primary order model:** last actual `source_type=ad` numeric source ID recorded on an inbound message for the canonical customer within **30 days before order creation**. Two different ads at an identical latest timestamp are ambiguous and remain unattributed.
- Require an explicit SalesOrder → conversation link and agreement between canonical conversation phone and order phone. Never match by customer name, infer an ad from a product or reassign an old order to a current customer owner. No order relationship is rewritten.
- Multiple ads for one customer are retained. Per-ad unique lead counts de-duplicate canonical phones. Report-wide lead totals use a union across ads, so per-ad lead counts need not sum to the total.
- **CRM unique leads** are customers with an actual ad-bearing inbound referral inside the selected period; this is not the number of Lead database rows. Repeated provider messages are de-duplicated. `starts` in the report API is the number of provider-deduplicated referral events, displayed as **referral events**, not proof of a newly created WhatsApp session. Conversation history is cumulative per customer; actual Meta conversation starts remain the separate Meta metric.
- Inbound messages and accepted staff replies are provider-deduplicated and assigned to the latest preceding actual referral within 30 days. They are activity metrics, not additional leads. Unresolved phone identity counts are displayed. No duplicate referral history is inserted.
- Lead statuses/product interests are current values, not historic snapshots. Interested/order-requested counts use explicit matching statuses, never payment evidence. Product/status filters affect CRM evidence only; Meta expenditure cannot be partitioned by unreported CRM interests and remains the full selected-ad spend.
- **Confirmed orders:** one SalesOrder in CONFIRMED, PROCESSING, DISPATCHED or DELIVERED. Known cancelled, returned, voided or refunded orders are excluded. Pending orders are separate in detail records. Fulfillment and payment statuses remain visible and separate.
- **Verified sales revenue:** gross order total (including service/shipping/tax and after order discount) only for confirmed orders backed by same-currency VERIFIED SalesPayment records whose aggregate covers the order total, with nonempty verifier and verification timestamp. Never use lead “Paid”, payment screenshots, payment_status alone, or an unverified Shopify figure. Partial payments contribute no revenue until fully verified. Overpayments do not increase sales above the order total.
- Known partial/full Shopify refunds, VOIDED/refunded financial status, CANCELLED and RETURNED orders conservatively exclude the whole order from revenue and confirmed-order conversion until reviewed. The app has no authoritative refund-ledger allocation suitable for subtracting partial refunds here. Do not describe this report as net revenue or profit. Unrecorded external refunds cannot be inferred.
- Spend uses Meta reporting dates in the ad account timezone. CRM referrals/messages and order creation are grouped into the same local-date boundaries, converted to UTC for querying. Orders use **order date**, not the referral date or the later payment-verification date. Historic reports can change when payment verification/refund evidence changes.
- Leads and orders are period activity, not a closed acquisition cohort; conversions may exceed 100% because an order in the selected period can follow an earlier lead. The 30-day lookback can match earlier retained referrals.
- CPL = spend / unique leads; CPO = spend / confirmed orders; conversion = orders / leads × 100; ROAS = verified attributed revenue / spend. Zero denominators and missing numerators yield unavailable. ROAS is not net profit.
- Currency is explicit. Foreign-currency verified orders are shown separately in ad details and excluded from that account currency's ROAS. No exchange conversion occurs. Changes in cached spend currency suppress totals rather than add unlike currencies.
- Daily reach is unique people within that day and entity. It is shown only for single-day reports and is **never summed across dates or advertisements**. Campaign/ad-set metrics are fetched at their own level rather than adding ad reach. Daily spend is additive and shown in ad detail with an accessible tooltip per bar.
- Missing ad hierarchy can prevent grouping otherwise known referral IDs under campaigns/ad sets. Keep uncertain orders in the unattributed list; known ad IDs still report at the ad level without invented spend.

## Access and UI

`/crm/ad-analytics` and `/crm/ad-analytics/ads/<ad_id>` provide the responsive report and detail. Filters cover dates, reporting level, campaign/ad-set/ad selectors, product interest and lead status. Selectors show cached names and submit precise IDs. The main table is paginated (50), sortable, and horizontally scrollable on mobile. Detail inquiries/orders and unattributed lists show up to 100 with totals. CSV exports aggregated rows for the entire selection, not private message bodies. Formula-like CSV cells are escaped.

Administrators can view/export/sync and configure Railway variables. Other staff must receive an **explicit Ad Analytics section grant** using existing Staff & Access controls; legacy all-section defaults do not grant this new financial section. Such authorized staff can view only, not export or sync. Existing linked conversation/lead/order pages still enforce their own permissions. Creative images reuse the existing authenticated referral preview endpoint; staff lacking conversation access cannot retrieve those images. Every report response uses private/no-store. Credentials never enter HTML/JS/API output.

## Migration, deployment and rollback

1. Review the branch/PR; do not auto-merge or auto-deploy.
2. Back up the existing database according to your normal Railway process.
3. Apply `alembic upgrade head`. Revision **20261109_35**, following **20261108_34**, creates only `meta_ads_daily` and `meta_ads_sync` plus indexes/unique constraint. It never edits CRM/customer/order/payment/referral records. No token columns exist.
4. Deploy the app through the usual reviewed process with sync disabled. Verify login, inbox, dashboard, consent and sales pages.
5. Configure the separate Ads token/account on web and existing cron; enable synchronization, request a manual first sync, inspect account/currency/timezone/counts in Ads Manager, then verify a scheduled run.
6. If no existing scheduler is actually running, automatic synchronization is **not active** merely because code exists. Configure a command in an already paid-for scheduler. This change does not provision a new Railway service or subscription. Ask approval before choosing any new paid scheduling service.
7. Roll back: set `META_ADS_SYNC_ENABLED=false` on web and cron, remove/disable the Ads token binding if appropriate, redeploy the previous application revision. Keep additive reporting tables; downgrade is intentionally non-destructive. Prior app ignores them. Do not delete or modify sales data to roll back this report.

There is **no third-party connector subscription and no new paid infrastructure**. Official API usage remains subject to Meta rate/access policies. Processing uses existing web/database/cron resources and can consume ordinary hosting usage; no claim is made that hosting compute is free.

## Validation

Tests use httpx mock transports and isolated databases. They cover authentication/scope failures, account verification, GET-only calls, unsafe paging URLs, rate limits/backoff, all three levels, idempotence/empty refresh, partial failure preservation, numeric/currency validation, unavailable metrics, duplicate referrals, multiple ads, canonical identity, verified revenue/refund exclusion, ROAS and date boundaries. Route/UI tests exercise server authorization, CSRF, exports, additive migration and desktop/mobile rendering. No live Meta or WhatsApp calls are made by tests. Live asset/token validation and a scheduled production success require administrator setup after review.


### Recorded validation results

- 35 new Ads tests passed, including Chromium at 1366, 390 and 320 pixels, additive migration, safe exports, concurrent status claims and no-write identity normalization.
- 113 existing tests passed across dashboard analytics/UI, staff access, quick messages and worker diagnostics.
- Two existing learning tests failed and were independently reproduced on unchanged main `33a2892`: `test_manager_publication_review_edit_validation_and_withdrawal` (403 vs expected 409 after switching logins) and `test_chromium_zero_run_history_diagnostics_and_mobile` (non-zero-padded browser day vs expected zero-padded day). These are not changed by this branch.
- Python syntax, JavaScript syntax and whitespace checks passed. All Meta calls were mocked; no production API synchronization or scheduled production run was performed.

### Changed files

Foundation: `meta_ads_models.py`, `meta_ads_client.py`, `meta_ads_worker.py`, `alembic/versions/20261109_35_meta_ads.py`.
Reporting: `meta_ads_report.py`, `meta_ads_routes.py`, `templates/meta_ads.html`, `static/meta_ads.js`, `static/meta_ads.css`.
Integration: `main.py`, `learning_worker.py`, `staff_access.py`, `templates/base.html`.
Validation/documentation: `tests/test_meta_ads.py`, `tests/test_meta_ads_ui.py`, `docs/meta-ads-analytics.md`.
