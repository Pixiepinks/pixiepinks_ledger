# Phase 4F — physical Windows acceptance checklist

Record operator, Windows build, DBL model/serial, driver versions, USB port, agent source commit/build hash/signature, device/register/location, media sizes, and test time. Do not include tokens, passwords, customer phone numbers or card data in the acceptance record.

**Not performed in the Linux development environment.** Automated tests use mocked Windows and Shopify. Do not mark a mock test as physical driver, cut, paper or electrical verification. No Phase 4F task authorizes turning on Shopify checkout/refund writes or creating arbitrary live test orders. Use non-financial printer tests, authorized existing COMPLETE receipt duplicates and approved label batches first. Full normal-sale acceptance requires its separate ordinary checkout release or an isolated development shop/test database, never a fabricated production COMPLETE state.

## Preconditions

- [ ] Reviewed source and migration 20261120_48 approved; no live deployment assumed.
- [ ] Existing POS, checkout/refund, product/barcode and inventory settings recorded and unchanged.
- [ ] New three hardware gates initially false; operator approval recorded for each hardware test/activation.
- [ ] Dedicated standard Windows agent account differs from cashier/browser users; DPAPI profile loaded.
- [ ] Signed/reviewed agent executable or explicitly reviewed development venv; no security protection disabled.
- [ ] Normal queues are exactly POSPrinter POS-80 and 4BARCODE 3B-365B, using the same verified local USB/DOT4 port.
- [ ] Both normal queues have Cash Drawer=None, including effective agent-user preferences.
- [ ] Only isolated PixiePinks Drawer Control (POS-80) uses tested vendor Cashbox #1 after-print behavior. Its ACL permits only the agent account and administrator/SYSTEM, not cashier/browser accounts.
- [ ] Isolated drawer queue uses Print directly to the printer, Keep printed documents is off, and manufacturer-supported interrupted USB/spooler no-replay behavior is verified before local drawer attestation.
- [ ] Receipt cutter is per page/Semi Cut, feed 15 mm; label calibration/density/dimensions are separately tested.
- [ ] Actual cash drawer electrical setup/manufacturer driver is confirmed and key is Under Control. No guessed pulse/voltage/raw command.
- [ ] All queues drained/inspected, correct physical media installed and acknowledged in CRM; no competing external printing.

## Pairing/security

- [ ] Pair code expires after five minutes and cannot be used twice; device remains inactive until administrator activation.
- [ ] Agent authenticates without admin cookies/Shopify tokens, using outbound HTTPS only; no local listener/firewall exposure.
- [ ] Credential and attestation files are DPAPI-protected; copying them to a different user cannot authenticate/decrypt.
- [ ] Changing a driver, USB binding or exposed effective DEVMODE setting blocks until local reattestation.
- [ ] Rotation invalidates old auth; revoked device cannot claim documents or reactivate under the same ID.
- [ ] Wrong register/shop/device job requests are rejected. No unlimited historical receipt sweep occurs on enrollment.
- [ ] Cashier/browser account cannot print to isolated drawer queue. Do not test this using a genuine kick; inspect access/queue visibility and use denied authorization, retaining admin/SYSTEM access.

## Printing and media

- [ ] Non-financial receipt test prints on POSPrinter POS-80 with correct 80 mm stock, readable text, 15 mm feed and Semi Cut; drawer stays closed.
- [ ] 58 mm stock/profile is tested separately if needed; wrong configured width is blocked, not shrunk.
- [ ] Authorized existing COMPLETE duplicate prints, marked DUPLICATE / REPRINT, without opening drawer.
- [ ] A4 only targets a separate suitable attested A4 printer, explicitly in A4 mode. If no A4 printer exists, local A4 remains unavailable; browser PDF fallback is preserved.
- [ ] Switch to labels only after changing stock/calibration and draining both queues; agent acknowledges operator mode, does not claim automatic detection.
- [ ] Existing generated label batch routes exclusively to 4BARCODE 3B-365B. Verify product, exact barcode value/leading zeros, SKU, variant/compact layout, price, dimensions, quiet zones and copies.
- [ ] Scan printed Code 128 labels into the existing POS. Shared supplier codes still require explicit variant selection; repeated unique scans increase quantity.
- [ ] Labels never open drawer. Barcode raster/driver geometry is not fit-scaled; mismatches block before StartDoc.
- [ ] Receipts wait while label stock is selected. Label jobs wait in Receipt mode. Simultaneous/conflicting jobs are not submitted across the shared queues.
- [ ] Windows acceptance is reported SUBMITTED_TO_PRINTER only; physical confirmation is explicit and audited.

## Controlled drawer actions

These steps are real physical commands and require an authorized operator, approved hardware gates and verified isolated queue. The implementation prints a small non-financial control slip; accept its additional feed/cut.

- [ ] Explicit authorized manual opening/test with an open assigned register, reason and one unique action ID opens once.
- [ ] Ordinary unprivileged cashier cannot manually open without pos.drawer.open. All openings retain cashier/register/device/reference/reason/time/outcome audit.
- [ ] Ordinary non-financial receipt printing and every duplicate leave drawer closed.
- [ ] In separately approved normal-sale/development-shop acceptance, a verified cash sale opens once after receipt submission; card-only and bank-only do not; verified split with positive cash opens once.
- [ ] Held/unpaid/uncertain sales never produce a final receipt or drawer action.
- [ ] Approved paid-in/out opens only when separately enabled and recent; it does not create another cash or GL movement.
- [ ] Late financial reconciliation, expired recent-cash window or closed original register does not trigger a delayed automatic drawer opening.

## Recovery and status

- [ ] Unplug/reconnect USB and reproduce Offline/Paper-out/Spooler error safely using non-financial jobs. A queue may initially show stale Ready; no physical success is claimed from it.
- [ ] Sale stays COMPLETE when printing fails; no extra Shopify order, refund, inventory adjustment or payment retry occurs.
- [ ] Disconnect networking before/after delivery and acknowledgement; unresolved action becomes UNKNOWN, not redelivered.
- [ ] Restart/kill the agent after durable INTENT; it records UNKNOWN and never replays the physical action on startup.
- [ ] Lost acknowledgements resend only status. Duplicate job IDs do not print or open twice.
- [ ] Long label batches keep progress while healthy; revoked/changed authorization blocks subsequent pages. Already spooled pages require physical inspection.
- [ ] Stop the agent, review all relevant queue documents (UUID) and actual output before resolving UNKNOWN. Any new drawer opening has fresh explicit authorization; never delete local action history/reset attempts.
- [ ] Partial labels are counted/inspected and only remaining copies use a new preview-confirmed PDF. Never replay the old uncertain PDF automatically.
- [ ] Re-pair/replacement cannot bypass an unresolved old-register action.
- [ ] Review/audit remains available while hardware dispatch gates are off.

## Startup, upgrades and rollback

- [ ] Dedicated-account scheduled task works with profile/DPAPI after logoff/reboot, no second instance or unwanted UAC/prompt.
- [ ] Printer preference changes require attestation; rollback keeps normal Drawer=None and blocks all new dispatch.
- [ ] Uninstall/revocation stops future auth, retains action journal/audit and does not remove working receipt/label drivers or financial data.
- [ ] Owner/accountant/Windows administrator acceptance signed off. No live checkout, merge, deployment or physical drawer test is attributed to automated development tests.

Record observed outcomes and unresolved driver/hardware limits. Do not mark production activation ready until the actual required Windows behaviors pass.

## Isolated drawer transport prerequisite

The administrator must set the isolated drawer queue to **Print directly to the printer**, turn **Keep printed documents** off, and obtain manufacturer-supported evidence and authorized physical interruption tests showing that USB/spooler recovery does not replay the driver cashbox action. The agent checks the direct/no-retention queue attributes and requires a separate local no-replay attestation. Unknown or unsupported behavior leaves drawer automation blocked; receipts and labels can be accepted independently. The application journal prevents application retries, but cannot establish exactly-once physical behavior inside a Windows driver or printer firmware. No physical interruption test or drawer action was performed during development.
