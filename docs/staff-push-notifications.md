# Staff notifications and Android installation

Internal alerts extend the existing CRM. They never send customer WhatsApp messages, enable Global AI, run Saved Flows, or change assignment/consent permissions. No paid notification provider is required.

## Deployment and Railway configuration

1. Install the updated `requirements.txt` (pywebpush and cryptography).
2. Apply additive migration **20261111_39**, after 20261110_38, with `alembic upgrade head` before starting the updated application. It creates six staff-only tables: preferences, controls, encrypted devices, live inbound markers, notification history and per-device delivery attempts. It is repeatable and leaves existing records/settings untouched. Reminder/escalation controls seed **OFF** and both recipient lists start empty.
3. Generate a stable VAPID key pair in a trusted terminal, without displaying the private key in chat/logs:

   ```sh
   python scripts/generate_staff_push_keys.py \
     --output /secure/path/pixiepinks-push.env \
     --subject mailto:operations@your-business-domain.example
   ```

   The command creates a new file with owner-only permissions and refuses to overwrite existing files. Store this file securely; do not commit it. Copy its values directly into Railway Variables using your secret-management process.

| Variable | Value / purpose |
| --- | --- |
| `STAFF_PUSH_ENABLED` | `false` during deployment; explicitly change to `true` when ready to test device push |
| `STAFF_PUSH_VAPID_PUBLIC_KEY` | URL-safe uncompressed P-256 public key from the generated file; intentionally public |
| `STAFF_PUSH_VAPID_PRIVATE_KEY` | URL-safe raw P-256 private key from the file; server-only secret |
| `STAFF_PUSH_VAPID_SUBJECT` | Real `mailto:` operations contact or HTTPS contact URL |

Keep the existing `SECRET_KEY` secure and stable: a separate purpose-derived key encrypts subscription endpoints and browser encryption keys at rest. Rotating `SECRET_KEY` invalidates old subscriptions; staff must register again. Rotating VAPID keys also requires device re-registration. No service account or Firebase paid account is needed. The push service is selected by the browser.

4. Use the existing HTTPS origin. Permit server HTTPS egress to `fcm.googleapis.com` (Android Chrome), optionally `updates.push.services.mozilla.com` and `web.push.apple.com` for other supported browsers. Redirects and arbitrary subscription hosts are rejected. Chrome also needs its own working connection to its push service.
5. The normal FastAPI startup starts a separate daemon loop using the existing in-process background-worker pattern; no extra cron or Railway service is required. It checks durable work every ten seconds. Multiple app workers are supported through unique constraints and conditional claims. The normal question worker is unchanged.
6. Open the header bell → **Notification settings**. An Inbox manager (existing ADMIN/SALES_MANAGER with Inbox access) selects shared Inbox recipients and escalation managers. These lists do not grant conversation access. Ordinary staff can manage only their own preferences/devices. Unassigned conversations normally require an administrator or an existing explicit share; escalation recipients also need existing conversation visibility.
7. Test a limited staff device first. **Keep Enable reminder delivery and Enable escalation delivery unchecked until their operational behavior is manually approved.** Defaults are five and fifteen minutes; managers may configure 1–1440 minutes with escalation later than the reminder. Enabling push does not enable these controls.

## Exact Android Chrome steps

1. Open the production CRM URL in current Chrome on Android over HTTPS and sign in with your own staff account.
2. Chrome menu **⋮ → Install app** (or **Add to Home screen → Install**). Alternatively use **Install CRM** on Notification settings when Chrome offers the install prompt. PWA installation is useful but is not required for Web Push support.
3. Open the installed CRM, then tap the header bell → **Notification settings**.
4. Tap **Enable this device**, then Android/Chrome **Allow**. This must be a deliberate tap; the CRM never requests permission automatically.
5. Enable the desired message/assignment/reminder preferences and save. Sound defaults OFF; enable it if wanted. Tap **Enable sound in this open CRM** to unlock foreground audio. Each open tab needs a user interaction; an ordinary tap can unlock audio when the sound preference is enabled.
6. Tap **Test notification** (rate limited to once per minute). Check the notification shade and tap the alert. Test alerts open settings; customer alerts open the exact conversation.
7. If permission is blocked: Chrome **⋮ → Settings → Site settings → Notifications → this site → Allow**, or Android **Settings → Apps → Chrome/PixiePinks → Notifications → Allow**. Return and enable the device again. Re-enable after clearing site data, key rotation or a subscription-renewal failure.
8. On each additional phone, sign in and repeat device enablement. Up to ten active subscriptions per staff account are supported. Remove a device from settings to revoke it server-side. Logout/account changes revoke this browser's registration and clear displayed alerts where the worker is available.

A running Chrome tab is not required for supported Web Push delivery. Android/Chrome and network conditions ultimately control background and lock-screen delivery. Force-stopping Chrome, denying background activity, battery optimization, Silent Mode, Do Not Disturb and system notification categories may delay/suppress display, sound or vibration. Web Push cannot provide arbitrary custom lock-screen sounds. The PWA does not cache CRM pages, messages, media or credentials and is not an offline CRM. Authenticated preview fetching requires a working connection and a valid login; expired sessions require sign-in. Android may show a generic browser background-update notice when no private preview may be displayed.

## Event and reminder behavior

- A newly persisted, signed, live inbound message creates an idempotent live-message marker and an alert for the current owner, or eligible configured shared recipients when unassigned. Historical imports, unsigned internal stores and outbound messages do not trigger push alerts. A first-time live webhook delivery still alerts if Meta delivered it late; receipt retries remain deduplicated.
- Message receipt IDs remain the webhook deduplication authority. Alert uniqueness is additionally `(recipient, event_key)`. Each device has a unique delivery per alert. No HTTP request occurs in the webhook/assignment transaction. Notification inserts use a nested savepoint: if the notification store fails, the customer message/assignment still commits, and `STAFF_NOTIFICATION_ENQUEUE_FAILED` is logged. That exceptional case needs operator investigation; it is not claimed as lossless notification delivery.
- Assignment notifications are emitted after authorized ownership changes, including ad-based assignment. They distinguish assignment from message alerts and identify the assigning staff member. No-op requests, self-assignment and assignment to nobody produce no assignment alert. Reassignment invalidates pending alerts to the old owner/team, even if that person still has a share.
- Reminders reuse the existing operational reply queue, count from the first relevant live unanswered inbound message, and stop when a successful reply clears that turn or staff resolve/defer the conversation. Subsequent inbound messages do not reset an existing unanswered turn. Assignment versions direct a new reminder to the new owner. Escalation is unique per unanswered turn and goes only to eligible configured managers. Old pre-feature/imported messages do not create a reminder blast; a new live inbound starts an eligible clock.
- Preferences and current conversation access are rechecked at delivery, preview and tap time. The bell shows an exact currently accessible unread count and up to 100 recent accessible alerts; staff can mark each read. Removing access hides the associated preview/history. No notification setting grants access.

## Reliability, privacy and diagnostics

Subscriptions are encrypted in the database; APIs expose only device labels/status, not endpoint URLs or encryption secrets. Push-service payloads contain only an opaque alert ID. The service worker fetches the customer name/safe preview from an authenticated, device-bound, no-store endpoint. Text previews remove URLs and obvious credential-like strings; media uses generic labels (including voice/image/video/document). No media is fetched. Staff must still treat lock-screen previews as visible to people near their phone.

Attempts are recorded as PENDING/SENDING/SENT/FAILED/EXPIRED/CANCELED. SENT means the push service accepted the request, not proof the Android device displayed it. There are at most five HTTP attempts, with 15/30/60/120-second backoff, a 45-second crash lease and a five-minute alert expiry. A lost HTTP response or crash after provider acceptance can result in another opaque push; the same tag plus a one-day IndexedDB ID ledger suppresses repeated visible alerts even after dismissal/restart. This is at-least-once transport with deduplicated display, not a claim of exactly-once delivery. No database transaction is held over provider HTTP.

HTTP 400/404/410 and locally invalid subscriptions revoke the device and erase its encrypted subscription. Other permanent client errors fail the delivery; 408/429/temporary transport errors use bounded retry. Delivery logs retain only fixed status codes, not raw provider responses, subscription URLs, customer bodies or tokens. Device settings show last status; server logs use fixed `STAFF_NOTIFICATION_WORKER_FAILED` / `STAFF_NOTIFICATION_ENQUEUE_FAILED` codes. DB history retains references, not copied message bodies.

If alerts are missing, check server configuration, Android permission, the active registered device, its current session/key versions, notification preferences, current ownership/shares, shared-team recipients and worker logs. Check reminder controls separately. Notices older than five minutes are not pushed after an outage; review history/Inbox instead. Browser-origin authentication cookies and CSRF protect registration, preferences, controls, revocation and read actions. The current application is single-business; no second business/tenant scope is introduced.

## Manual Android acceptance checklist

Use a test customer number and an authorized test staff account after deployment; local automated tests do not certify real Android display.

| Scenario | Check |
| --- | --- |
| CRM open | New inbound updates bell/history; foreground sound works after interaction with sound enabled; tap opens the chat |
| CRM in background | New message and assignment create distinct Android notifications |
| Screen locked | Notification appears according to Android privacy/sound settings; tap unlocks/opens the correct conversation |
| Chrome tab closed | Browser push works where Android allows Chrome background delivery |
| Wi-Fi/mobile temporarily disconnected | Reconnect within TTL and check permitted delivery; stale alerts are not replayed indefinitely |
| Silent mode | Visual alert may appear; no audible guarantee |
| Do Not Disturb | Android policy is respected; no audible guarantee |
| Assignment/reassignment | Only the new owner gets the assignment; no duplicate on repeated save; no self-assignment alert |
| Duplicate webhook | One history alert/device delivery |
| Two phones / logout / remove | Both enabled devices work; revoked browser cannot fetch later private previews |
| Reminders (separate approval) | Test 5/15-minute thresholds, reply/resolve cancellation and reassignment; leave disabled until approved |

## Tests and rollback

Automated tests use isolated databases and mocked provider transport. Chromium checks desktop, 390px and 320px layouts and service-worker event behavior. Optional PostgreSQL tests use the existing disposable test fixture with `AD_FLOW_TEST_DATABASE_URL`, restricted to a loopback `pixiepinks_test` database and unique temporary schemas. No live notifications or production variables are touched by the test suite.

For rollback, set `STAFF_PUSH_ENABLED=false`, disable both reminder controls, and stop updated worker processes before restoring prior code. Retain the additive tables for review; downgrade intentionally preserves them. Remove devices/unsubscribe or unregister the service worker if retiring the feature. Existing CRM workflows, ownership, Global AI and customer messages are untouched.

### Recorded verification (11 November 2026, Sri Lanka)

- Existing relevant regression run: **373 passed, 3 skipped**, covering Inbox/assignment/shares/resolution, ad assignment and Saved Flows, staff permissions, Saved Voices, customer questions, Global AI, marketing consent, Quick Messages and Meta Ads.
- The three skipped PostgreSQL checks were subsequently run against disposable PostgreSQL 16 and passed. Together with the two new notification database checks, the PostgreSQL run was **5 passed**. The existing Saved Voices migration assertion now follows Alembic's current head rather than the obsolete hard-coded revision, retaining its audio and concurrent-send checks.
- Final notification tests: **37 backend tests passed**, including real pywebpush encryption/VAPID construction with HTTP mocked; **5 Chromium checks passed**, including desktop/390px/320px rendering, safe tap/read navigation, persistent display deduplication and authenticated subscription renewal. These overlap the broad run and must not be summed as unique tests.
- Final registration API recheck, Python/JavaScript syntax and whitespace checks passed. No real push endpoint, customer WhatsApp transport, production database or environment variable was used/changed. Real Android background/lock-screen behavior remains the deployment acceptance checklist above.
