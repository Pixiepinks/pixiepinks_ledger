# Shopify-original labels and profile-safe previews

## What changed

The old profile change handler redrew form settings only. It did not clear the old iframe/PDF URL or server preview token, did not regenerate automatically, and did not guard against out-of-order responses. The backend already checked configuration/row equality before job creation, but the displayed PDF could remain from an older profile.

Every profile, barcode-source, quantity/copy or custom-setting change now invalidates the PDF/token immediately and disables Generate. A profile/quantity/source change regenerates using the actual selected profile. A monotonic request revision discards late results, including a tested older 50×30 response arriving after a 30×15 selection. Each displayed PDF has a unique object URL and preview identity; requests/responses use no-store caching. The active title, physical dimensions, loading/error state and preview identity appear above the iframe. The server rechecks the exact full rows/copies/configuration against the preview before reserving a batch.

## Original barcode workflow

1. Open **Barcode Label Printing** using existing product-view and label-print grants.
2. Focus **Scan original Shopify barcode**, scan + Enter. This is a catalogue lookup, not a Shopify or barcode modification. Only the exact imported Shopify barcode is matched. It is never replaced with the local code, even when a local assignment remains active.
3. A unique active match populates the frozen selection and shows product/variant, original SKU, exact barcode string, selling price/currency and catalogue sync time. Refresh the existing Shopify catalogue import first if prices/barcodes changed; this feature does not claim a live GraphQL price read. Stale/missing/ambiguous records fail closed. Original-mode duplicate checks include conflicting other variants' local codes; even documented shared supplier values must be reconciled before this original-label flow can print.
4. Choose source **Original Shopify barcode — never substitute local** and the desired profile. A scan chooses that source explicitly. When the current profile is the legacy default50×30, scanning selects the new full-content 50×30 original option at the same dimensions. A previously chosen small profile is not silently enlarged or shrunk.
5. Select quantity/copy overrides. Review the refreshed PDF, then Generate PDF batch. Browser/manual PDF printing remains unchanged. Download a generated batch and print **Actual size / 100%, portrait, matching driver paper**, never Fit to page.

No Shopify API writes, assignments, SKU updates, inventory movements, prices, POS records or purchase records are changed. Normal effective-barcode printing is still available as an explicit source and retains local preference. No print agent, cash drawer or automatic printing is activated.

## Profiles and physical limits

All existing profile configurations/rendering remain intact, including old `30x15-compact-sku-price` (5pt name, barcode, barcode | SKU, enlarged price; no brand), 25×15, standard30×15, 40×20, 50×30 and60×40. Standard30×15 has a clearer menu title only. Using original source with a legacy profile does not silently change its field visibility/layout.

Two additive profiles offer the full requested content:

* **30x15 Compact — SKU & Price (Shopify original)** (`30x15-shopify-original`): PIXIEPINKS, shortened name, vector Code128, exact human barcode, original SKU left and bold price right. Physical PDF page30×15mm. Margins0.5–0.75mm; calibration must retain at least0.5mm. Barcode height5mm, minimum two203dpi dots/module, ten-module quiet zones each side. Brand/name/barcode text/SKU minimum4.5pt; bold price adapts7→5pt and never below5pt. Only product name can shorten; barcode/SKU/price cannot truncate. Unfit required content produces an actionable error.
* **50x30 Shopify Original — SKU & Price** (`50x30-shopify-original`): existing vector renderer with brand/name/variant/barcode/SKU/price enabled, for longer original codes. It is additive; legacy50×30 is unchanged.

A smaller font does **not** make a barcode narrower. Code128 width depends on the exact encoded symbol and printer-dot module width:

| Source value | Code128 plus quiet-zone width at203dpi /2dots | 30mm result |
|---|---:|---|
| `4792210130832` | about35.79mm before margins | Reject; use50×30 or another validated larger layout |
| `012345678901` | about30.28mm before margins | Reject; use a larger profile |
| `AB12` | about24.77mm before margins | Fits the new compact geometry |
| `000012` | about22.02mm before margins | Fits; leading zeros retained |

Thus this improvement cannot honestly print every12/13-digit Code128 on30mm stock while retaining two-dot modules and quiet zones. It displays **Barcode too long for this label profile** instead of shrinking to one-dot bars, truncating values, switching symbologies or stretching the PDF. Longer alphanumeric values may exceed even50/60mm; use a properly configured physically supported larger layout, not forced scaling.

## Source and reprint safety

Original mode uses current imported Shopify `barcode`, `sku`, `price`, `currency` and exact variant identity, not local fallbacks. Snapshot rows retain the source marker so subsequent generation/download/reprint and existing hardware admission checks compare against the appropriate original barcode. If the imported barcode, SKU, price, currency or variant identity changes, an original snapshot cannot be downloaded/reprinted as if current: scan and preview a fresh batch. Existing effective-source history retains its previous semantics. Leading zeros and case are strings throughout.

## Samples, tests and acceptance

[Synthetic three-page30×15 PDF](samples/shopify-original-labels/compact-30x15.pdf) · [13-digit original example on50×30](samples/shopify-original-labels/original-13-digit-50x30.pdf). Samples are fixture data, not proof of current live prices or physical printing.

Automated tests independently decode rasterized PDFs at203dpi with zxing-cpp, assert exact physical dimensions/vector bars/minimum fonts, cover leading zeros/alphanumeric/numeric widths, long SKU/name/price, missing price, duplicates, permissions, explicit source vs local preference, batch contents and stale-price/token rejection. Chromium exercises profile refresh, old-response rejection, scan population and quantity/batch selection. Final results are in the PR.

**Physical acceptance is pending:** after separate release approval, install/select the DBL365B Windows driver, matching paper dimensions and gap calibration. Disable any automatic scaling. Print one authorized synthetic label on actual30×15 stock containing a short fitting code, scan it into Notepad and the CRM and compare exact string including zeros. Check name/SKU/price, margins and quiet zones. Print the13-digit sample only on matching50×30 stock; verify exact scan. Inspect batch counts and alignment. Record actual physical results before claiming printer acceptance. No physical printing was performed during development.

## Deployment and rollback

No migration; Alembic head remains20261122_53. Review and approve PR/deployment separately. Keep all Railway variables, Shopify write flags and parked printer integration unchanged. Hard-refresh/restart the desktop browser after deployment to load updated JavaScript; reselect a profile and regenerate previews. Preview tokens expire after ten minutes and cannot authorize a changed batch.

Rollback the application to the previous reviewed commit without changing source data or replaying jobs. Preserve generated PDF/job evidence. New custom layout snapshots cannot be rendered by old code: keep them for evidence; do not reprint them through an old renderer. Already generated PDFs remain stored, but an old application lacks the original-source metadata safeguards—keep original-source jobs from download/reprint/hardware release until supported code is restored. Do not force old label snapshots through another layout or reactivate hardware printing as a rollback workaround.

### Final validation results

* Broad original/legacy label, scanner, retirement, barcode-sync, PostgreSQL, daily POS and hardware integration run: **229 passed**, no failures/skips after restoring the task-owned local PostgreSQL fixture. The initial run's PostgreSQL connection errors were environmental and were resolved before the successful rerun.
* Final focused original-label/Chromium suite: **31 passed**, including exact12/13-digit/leading-zero decoding on larger stock, compact rejection, source/price safety, batch decoding and preview races. Counts overlap the broad run.
* All original preset configurations are asserted unchanged; the old compact font/content regression passes. Python compilation, JavaScript syntax and diff checks passed. Physical printer/scanner validation remains pending.

Changed implementation files: `retail_labels.py`, `retail_routes.py`, `static/retail.js`, `templates/retail.html`; tests in `tests/test_shopify_original_labels.py`; this runbook and two sample PDFs. No POS, inventory, accounting, purchasing, Shopify adapter or migration files were changed. The stopped WhatsApp investigation's uncommitted files remain untouched in its original worktree; this work uses a separate branch/worktree.
