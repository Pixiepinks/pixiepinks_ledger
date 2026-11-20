# 30×15 Compact — SKU & Price: prominent adaptive price

Only `30x15-compact-sku-price` / `compact_sku_price` changes. The standard 30×15
profile and all other preset rendering remain unchanged, including their fonts.
Saved copies using this compact content layout retain their existing configuration
and calibrated margins. No new profile, migration or environment variable is added.

## Layout and sizing

Product name remains Noto Sans Regular 5 pt with the same safe name shortening.
Code128 remains exactly 5 mm tall with identical x/y/bar widths, at least two-dot
modules and ten-module quiet zones. Barcode | SKU stays Noto Sans Regular 4.5 pt,
at the same original position with no truncation. Prices come from the same
Shopify variant price/snapshot and are formatted exactly as before to two LKR
decimal places; no assignment, SKU, price or inventory is edited.

Only price uses the newly bundled, licensed Noto Sans Bold. Font source/checksum
and SIL license are documented in `static/retail-fonts/SOURCES.md` / `LICENSE`.
It is embedded in the compact PDF; no runtime network or Windows font dependency.
Other profiles do not load it.

The fitter uses the actual price string's bold glyph ascent/descent, measured
advance width, and the remaining physical band below the unchanged identifier
cell. It reserves two 203-dpi dots above the price, one extra dot inside the bottom
and horizontal margins, and snaps the baseline to the printer's dot grid. It
chooses the largest 0.01 pt candidate satisfying all constraints, never globally
scaling a label or moving the barcode. If even 5 pt cannot fit, the existing clear
validation failure remains; no price digits are clipped, shortened or changed.

Default margin 0.625 mm examples:

| Exact selling price | Previous | Improved |
| --- | ---: | ---: |
| Rs. 350.00 | 5 pt regular | 12.82 pt bold |
| Rs. 1,860.00 | 5 pt regular | 10.86 pt bold |
| Rs. 15,500.00 | 5 pt regular | 10.86 pt bold |
| Rs. 125,000.00 | 5 pt regular | 10.86 pt bold |

These exceed the requested 8–10 pt target where ink geometry safely permits.
Longer prices shrink adaptively; configured margins 0.5–0.75 mm and calibration
can yield different maxima. Name-size configuration remains 5 pt; the compact UI
hint now explains the adaptive price rather than incorrectly promising 5 pt.

## Before/after artifact and verification

[Comparison PDF](evidence/compact-price-comparison.pdf) contains eight actual
30×15 mm pages. For each price, the odd page is the previous regular 5 pt layout;
the next even page is improved. Bookmarks identify old/new size without adding
text inside the physical label. [Page/size metadata](evidence/compact-price-comparison.json)
records the mapping. All labels are synthetic SAMPLE Water Book / S1 / SAMPLE001.
They are not inventory assignments or real product-price changes.

Regenerate with `python -m scripts.generate_compact_price_comparison`; this writes
review artifacts only, not print jobs. The previous routine is retained only in
that sample utility. Main rendering uses the adaptive fitter.

Tests cover all four requested prices plus a longer amount across all three
supported margins, true PDF dimensions, actual glyph bounds/centering and clearances,
bold price/unchanged regular name and identifiers, and an independent Code128
reader at 203 dpi. They verify an additional 0.01 pt cannot satisfy the same
width/clearance/grid constraints. All eight comparison pages decode to S1.

Committed pre-change fixtures from main `1ed41e2` capture barcode coordinates,
name/identifier text matrices, other-profile drawing-stream hashes and 203 dpi
raster hashes. They make tests independent of historical git objects in shallow
CI checkouts. Every other preset matches its original drawing stream and rendered
pixels. Missing/invalid currency/price/SKU, unsafe long identifiers, profile/API,
price source and print-job controls remain covered by existing regressions.

## Approved release, physical check and rollback

No merge/deployment is performed by development. After separate release approval,
deploy the reviewed commit through the normal process and reload the label page
for the versioned helper text. No database or driver changes are required.
Existing retained PDFs stay unchanged; newly generated compact PDFs use the new
price. Existing other-profile PDFs and rendering stay unchanged.

For separately authorized physical acceptance use the comparison sample, the
existing tested DBL 365B label queue/driver and 30×15 mm stock (about 1.1811×0.5906
inches), Actual size / 100%, one copy per selected page, no Fit/Shrink. Preserve
calibration, gap, density and barcode module settings. Do not use the POS-80 receipt
or drawer queue. Check price readability, all SKU/barcode characters, intact
quiet zones and actual scanner output at normal working distance. Automated
203 dpi decoding is not physical printer/scanner certification. No physical
hardware command or production transaction was executed here.

Rollback restores the reviewed code/font/helper release and reloads the page;
no data rollback. Retain existing print jobs and audits. Never change assigned
barcodes, prices or SKUs to undo label typography. No Shopify writes, print-agent
activation, cash-drawer commands, live inventory or financial updates are involved.

## Validation results

- Final compact/existing-profile/API/browser/retail/product-selection regression
  run: **110 passed**.
- Final maximal-size, original-field font/position, raster parity and comparison
  artifact checks: **18 passed**, seventeen overlapping the main run and one new
  comparison-PDF case. **111 distinct passing cases**, no skipped cases.
- Earlier focused compact run: 55 passed (overlapping). All requested prices and
  configured margins decode as Code128 at 203 dpi; all eight sample pages decode.
- Every other preset matches its pre-change drawing-stream and 203 dpi pixel
  hashes. Price-maximum checks reject a further 0.01 pt under the same dot-grid
  and clearance rules. Full-price strings, name/identifier fonts and coordinates,
  barcode rectangles, currency/source/assignment checks remain covered.
- A true-resolution 203 dpi before/after raster was visually reviewed; actual
  printer/scanner acceptance is still required. JS syntax and diff checks pass.
