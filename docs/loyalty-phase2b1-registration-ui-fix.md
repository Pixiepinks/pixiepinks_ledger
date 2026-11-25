# Phase 2B.1: purchaser registration UI and failure handling

Baseline: merged PR #210, main `aa13ced46f4fed527ccfa1feabf7e11360d80ace`; migration remains `20261125_58`. Branch: `feature/loyalty-phase2b1-registration-ui-fix`.

## Findings and evidence boundaries

1. **Confirmed source defect, reproduced in Chromium:** `loyalty_order.js` displays the browser's raw fetch exception. An aborted customer lookup reproduces “Failed to fetch”; there is no dedicated lookup state or retry. An unsuccessful lookup cannot establish that a Customer is absent. HTTP permission failures are a different path and do not establish a networking defect.
2. **Confirmed misleading country failure handling:** the drawer performs a separate CSRF GET plus country POST and maps every error to “Customer country unavailable.” The backend already normalizes valid bare `94…` numbers correctly; isolated tests return `LK`. Aborting the country POST reproduces the unavailable label with a valid Sri Lankan number. This does not prove a phone-parser defect or missing Shopify permission.
3. **Confirmed CSS defect:** `.loyalty-order-box input` gives every input 100% width and a 40px minimum height, including checkboxes. Baseline Chromium reproduces the oversized control. The fix targets checkboxes without changing global forms.
4. **Confirmed layout defect:** the registration section precedes the existing name/mobile fields. It is relocated immediately after those fields. The name/mobile inputs remain the single purchaser data entry point; optional alternate-customer search is collapsed by default and searches existing profiles.
5. **Register action:** the baseline authorized template contains the button unconditionally. No source condition explaining its absence for the production operator was established. It was not safe to claim a production permission/network/cache diagnosis. The revised prominent action has explicit states: visible only after a successful no-match check for the entered mobile, enabled only after purchaser confirmation. On failure, Retry is shown instead of implying a new customer.
6. **Asset version risk:** PR #210 changed assets while retaining old query versions. New versions are assigned to the registration JS/CSS and Inbox JS. This prevents reuse of the old URL in ordinary asset caches; it does not prove that a cache caused the reported production failure.

A synthetic baseline test using the actual pre-fix code reproduced raw fetch failure, misleading country failure and a >=40px checkbox. This was one passing **defect reproduction**, not a claim that broken behaviour was acceptable. The before Android screenshot captures that reproduction.

**Exact live transport root cause remains unverified:** no authorized production browser session or Railway runtime was available. Local API tests return success with authorized accounts and 403 with denied accounts. Do not change credentials, permissions, proxy trust, Railway variables or CORS based on an assumed cause. If production still fails after approved deployment, use the sanitized inspection below.

## Implementation

- Existing authorized customer search additionally returns normalized mobile and telephone numbering country. This is a read-only additive response; no new route, permission grant or mutation is introduced.
- Drawer uses that response for country and normalization. The old separate country POST endpoint is retained for compatibility, but this drawer no longer depends on its extra CSRF round trip.
- Separate loading, empty, matched, ambiguous, invalid/HTTP/network/redirect/JSON failure states. Retry cannot be confused with registration. No raw exception is logged.
- Successful registration reuses the entered purchaser name/mobile, explicit confirmation, existing actor-bound idempotency key and phone mutex. Success is shown only after the server response. Shared-phone cases require explicit selection/review.
- Request generations prevent stale lookup, selection and registration responses attaching to a new purchaser/order. Existing POS cart-version guards and registration workflow are retained.
- Country is labelled “Telephone country … (numbering region, not physical location).” Sri Lanka has a display fallback for older browser Intl implementations; other numbering countries retain international support.
- Unidentified-order controls move into collapsed Advanced options; existing saved reasons reopen it. Permissions, validation and required reasons remain server-enforced. Checkboxes are compact, and Save Draft remains visible on mobile.

Purchaser and local recipient inputs remain independent. No recipient phone is used for customer lookup, registration or loyalty. Shopify shipping mappings and delivery validation are unchanged. POS uses its existing `/crm/pos/api/customers` lookup/registration routes and separate modal; the shared registry remains behind those routes. Existing POS registration, walk-in reset and late-response cart guards passed browser tests. No evidence of a POS-specific production failure was supplied, and the POS implementation is unchanged. POS remains optional/walk-in capable, without loyalty discounts.

## Verification and screenshots

Final verification: **329 distinct tests passed**, zero final failures/errors/skips, including 11 PostgreSQL-module cases. The overlapping 56-case focused, 24-case final browser and 272-case regression runs are deduplicated; two screenshot cases repeat existing tests. Exact test results: `customer-registration-ui-fix-test-results.json`. The final runs include controlled network, 401/403/500, redirect and invalid-JSON failures; retry; loading and stale responses; Sri Lankan/international numbers; customer confirmation/registration; explicit ambiguous selection; existing membership; desktop/mobile layouts; CRM/POS; PostgreSQL concurrency and migration rollback; and relevant application regressions.

The optional-search browser helper now expands “Search another customer” before typing. The earlier recipient browser assertions expected only “Kuwait” and unnormalized form text; they are updated to assert the numbering-country label and normalized purchaser number. Delivery-recipient requirements are unchanged.

Screenshots use disposable synthetic data:

- [Before Android, forced network failure](images/customer-registration-fix/before-network-error-android.png)
- [Before desktop layout](images/customer-registration-fix/before-desktop.png)
- [After Android](images/customer-registration-fix/after-android.png)
- [After desktop](images/customer-registration-fix/after-desktop.png)
- [Confirmed registration, Android](images/customer-registration-fix/registered-android.png)
- [Confirmed registration, desktop](images/customer-registration-fix/registered-desktop.png)

Chromium mobile viewport testing is not real Android production acceptance. No paid production test, physical print or historical recovery was executed.

## Deployment (separate owner approval required)

1. Review the draft PR and test report. Back up according to existing release policy.
2. Confirm the existing migration `20261125_58`; **no new migration** or environment variable is required.
3. Deploy the reviewed commit through normal Railway release controls. Keep loyalty discounts OFF; do not change Shopify/printing/inventory flags or grants.
4. Reload the Inbox on desktop and Android. In Network, verify requested assets end in `v=phase2b1-1`. Verify all three updated JS/CSS assets return 200 with their correct MIME types. A full reload may be needed for an already-open tab.
5. Use an authorized existing customer inquiry; open Create Order. Expect normalized purchaser mobile, Sri Lanka/other correct telephone country, loading then explicit matches. No match is selected automatically. Do not save a commercial order merely to test the UI.
6. Verify no-match behaviour with isolated staging data, confirm identity, register, and verify server-confirmed feedback. Test actual production registration only for a legitimate customer with owner authorization; registration changes the Customer registry.
7. Check purchaser vs recipient separation, Advanced options, normal Save Draft footer, anonymous POS and existing member lookup. Do not enable discounts or recover the other historical orders.
8. Through the existing approved read-only Phase 2B verifier, confirm the retained member/order baseline and no unintended historical changes; account for genuine later purchases. Do not hardcode production IDs into new logic/tests.

### Sanitized live inspection if lookup still fails

An authorized Railway operator may verify deployed source identity without importing application startup or reading the database:

```sh
cd /app
python - <<'PY_READONLY'
import hashlib, json, os
from pathlib import Path
files = ['customer_registration.py', 'static/loyalty_order.js', 'static/loyalty_order.css', 'static/whatsapp_inbox.js', 'templates/whatsapp_inbox.html']
print(json.dumps({'mode': 'READ_ONLY_SOURCE_CHECK', 'deployment_commit': os.environ.get('RAILWAY_GIT_COMMIT_SHA'), 'sha256': {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() if Path(name).is_file() else None for name in files}}, indent=2))
PY_READONLY
```

Compare hashes with the reviewed feature commit (or main after its approved merge). A matching migration alone does not verify deployed route/static-source versions. This command does not verify request delivery, user grants, runtime workers or browser caching.


Use browser DevTools Network while opening the panel. Report only:

- Deployed commit and asset version; whether each expected asset loaded.
- Request **method and pathname only**, e.g. `GET /api/customer-registration/search` (omit query strings containing phone/name).
- HTTP status, redirect-to-login yes/no, MIME type, and whether the browser reports a connection/CORS/mixed-content failure. Do not copy raw request/response bodies, cookies, headers, CSRF tokens or full URLs.
- For successful lookup, only row count, conflict boolean and country code, not customer data.
- Effective signed-in role and whether existing Sales Orders create/edit grants are present, as booleans; do not change grants as a troubleshooting shortcut.

A request with no HTTP response requires browser/network/runtime evidence. HTTP 401/403 requires session/authorization investigation. A 200 HTML response or redirect requires routing/session investigation. A 200 valid JSON lookup with country LK but incorrect display requires actual JS/asset/console evidence. Do not equate these cases. Existing Procfile uses `railway_ingress:app --no-proxy-headers`; this feature does not change its security boundary.

## Rollback

Stop new registration attempts, redeploy the previous release and reload browser assets. Retain migration 58 and every Customer, membership, order, request-key, audit and mutex record. No database downgrade, financial reversal, Shopify resend, printer action, queue reset or historical recovery is needed. A lost registration response may already have created a Customer: retry the same idempotency key or explicitly search, never create another identity blindly.

## Production safety

No global discount activation, marketing consent, Shopify mutation, financial posting, printing activation, unread/AI/staff-assignment change or production data operation is part of this patch. Live failure causality and actual operator acceptance remain separate from the mocked/local verification.
