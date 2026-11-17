# DBL 365B — Windows/Chrome label setup and acceptance

The selected model is described as USB, direct thermal, 203 dpi, Code 128/TSPL label capable and ESC/POS receipt capable with a Windows driver. These are the provided specifications; no physical unit or exact firmware/driver was available during development. This release uses **PDF labels through the Windows printer driver** only. It neither opens USB nor sends TSPL/ESC/POS commands.

## Install and calibrate

1. Obtain the correct DBL 365B driver/manual from the vendor. Confirm the exact model/firmware, supported label media width, usable printhead width, minimum label width, gap/black-mark support and Windows version. Do not assume 25 mm media is mechanically supported merely because a PDF can be generated.
2. Connect USB according to the driver installer. Verify Windows recognizes the printer and can print the manufacturer's test page. Select barcode/label mode, rather than receipt/continuous-paper mode, if the driver distinguishes them.
3. Load **direct thermal labels**, thermal side toward the printhead, with guides snug but not bending the roll. Use the manual's feed/sensor calibration procedure. The exact button sequence must come from the purchased unit's manual; no universal sequence is assumed.
4. Add a driver paper form for the actual face size: 25×15, 30×15 or 50×30 mm (also 40×20/60×40 if supported). Set the physical gap, sensor type and feed behavior. Measure the label face and gap separately. A roll PDF's page is the face size; the driver/sensor handles the gap. In generic A4 mode the saved gap is vertical grid spacing.
5. Match 203 dpi. Start at the vendor-recommended speed and density, then adjust one setting at a time. Stored retail density/sensor/gap fields are setup notes for this driver and future backends; **PDF/Chrome cannot apply them to firmware**. The profile's 0–15 density is generic; translate only if the driver supports that scale.
6. Use a saved profile for each roll size and record driver version, speed, density, gap and calibration mode. Begin with one column; verify usable head width before enabling multiple labels per row.

## Chrome PDF printing

1. Download a generated PDF, open it in Google Chrome on Windows, press Ctrl+P and select the installed DBL driver. Use the system print dialog if custom media is missing from Chrome's dialog.
2. Choose the matching paper form, portrait orientation and **Actual size / 100% scale**. Disable Fit to page/Shrink to printable area, extra browser margins and headers/footers where those options exist. Print only a few labels initially.
3. Measure the physical label/bar position using a ruler or caliper. A 50×30 mm document must not have been scaled to A4, Letter or another roll form. Inspect clipping, vertical feed drift and skipped labels.
4. Adjust printer calibration first. If a small positional correction remains, save horizontal/vertical offsets inside the profile's margins. Do not compensate for a scaling problem with offsets or by reducing barcode module width.
5. A generated/downloaded PDF is not confirmation that paper printed. Use **Confirm physically printed** only after inspection. Reprint uses a retained snapshot, generates a new job and checks current scan-identifier safety.

## Scanner acceptance matrix

For **each** intended media size and scanner, record:

| Check | Method / pass condition |
|---|---|
| Exact value | Scan into a plain text field and compare character-for-character with the human-readable value, including leading zeros, case and punctuation |
| Symbology | Scanner enables ordinary Code 128; no GS1/FNC1 is added by this application |
| Short numeric/alphanumeric | Try the provided `P1` sample and your own approved numeric/alphanumeric identifiers |
| Existing manufacturer identifier | Try the exact existing value; choose a larger label if it cannot fit |
| Quiet zones | At least ten modules of clear white space on both sides; no edge clipping, artwork, adhesive damage or neighboring bars |
| Module size | At least two dots at 203 dpi; no Fit-to-page resizing |
| Read reliability | At least 20 consecutive correct reads for each representative label, at the operator's normal distance/angle, plus several off-axis attempts |
| Feed consistency | Print 30–50 labels; first and last must remain within the face without cumulative drift or skipped labels |
| Text | English/Sinhala name lines, variant, barcode value, optional SKU/price are readable and correctly associated |
| Durability | Check smearing, darkness, scuffing and placement on the intended products; direct thermal stock is heat/sunlight sensitive |

The 25×15 and 30×15 mm presets are compact. They intentionally omit some optional text and may fit only short identifiers. A digitally valid barcode is still rejected by the app if it cannot fit the configured dimensions/quiet zones. **Never shorten, overwrite or regenerate an existing assigned identifier to force a fit.** Use 50×30 or larger media after checking printer support.

Check any scanner-added symbology prefix or Enter/Tab suffix in its configuration; distinguish those from the encoded identifier. Do not interpret successful Code 128 scanning as GS1 issuance/GTIN validation.

## Troubleshooting

- **Blank output:** confirm direct thermal stock orientation, driver/model and label mode.
- **Labels skip/drift:** remeasure face/gap and calibrate the sensor with the actual roll; check drivers' continuous versus gap setting.
- **Barcode clipped:** correct paper form, 100% scaling and margins; choose larger media. Do not reduce modules below two dots.
- **Bars bleed or have gaps:** clean the head as directed by the vendor, check stock, then adjust speed/density gradually.
- **Long manufacturer barcode rejected:** this is the safe fit check; use a larger label, retaining the exact value.
- **PDF generation failed:** inspect job history and persistent storage/profile settings, then request an explicit reprint. This never sends to hardware automatically.

No hardware acceptance result is claimed by the software tests. Keep the signed-off matrix with your deployment record before staff use bulk labels. Receipt printing, automatic raw USB access and TSPL direct printing are not implemented in Phase 1.
