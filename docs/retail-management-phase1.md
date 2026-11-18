# Retail Management Phase 1

## Scope and architecture assessment

Base: `main`, `870b6a2a916008d9351ed5f856c0aba508c06751`. Feature branch: `feature/retail-phase1`.

The existing application uses FastAPI, SQLAlchemy, Alembic, server-rendered Jinja templates, vanilla JavaScript and shared CRM CSS. Production uses PostgreSQL; SQLite supports local tests. Session authentication loads active users and current database-backed `section_access` grants, with session version checks and CSRF protection. Existing background tasks use dedicated in-process threads.

`shopify_catalog_service.ShopifyCatalogClient` supplies server-side OAuth client credentials and the adopted `SHOPIFY_API_VERSION` (2026-07). Existing catalogue searches are deliberately small live lookups and are unsuitable as a persistent variant master. Accounting `Item` has only a name/SKU/unit and is not a Shopify variant master. No equivalent variant-master model was present.

New `retail_*` modules isolate master data, read-only import, local identifier allocation, PDF rendering, routes and the worker. Shared authentication, CSRF, sidebar, staff checkboxes and deployment startup conventions are reused. Existing messaging, AI settings, orders, accounting, Shopify order writes and worker modules are not redirected through Retail Management.

Migration `20261117_42` adds only retail tables, indexes and append-only triggers. No legacy field is renamed, backfilled or overwritten. The main compatibility risks are duplicate imported identifiers, partial imports, concurrent allocations, printer dot resolution, small media and hardware calibration. The implementation stages imports, locks allocation state, preserves every imported value and refuses unsafe barcode layouts.

## Available screens

Under **Retail Management**:

- **Product Master:** server-side search/filter/pagination, current Shopify identifiers, local identifiers, category mapping, metadata/details and history. Product links open Shopify Admin. The local record ID and shop/variant identity stay stable across imports.
- **Barcode Management:** frozen selections across pages, allocation preview and confirmation, documented duplicate-group exceptions, alternative-barcode review, identifier history and individual/bulk printing links.
- **Barcode Label Printing:** configurable profiles, selected variants paginated in groups of 50, per-variant copy overrides, a five-label PDF preview, queued bulk generation, downloads, reprints and explicit physical-print confirmation.
- **Product Audit:** distinct product/variant counts, SKU/barcode coverage, duplicate groups, scan conflicts, pending local identifiers, manual-review and stale counts, filters and streamed CSV.
- **Import & Sync History:** queued/running/complete/failed imports, actor, mode, timestamps, duration, heartbeat, scanned/staged/published counts and sanitized error reports. Imports require explicit confirmation.

Use the product Details action to inspect inventory item/tracking, options, collections, vendor, type, price/compare-at price and Shopify update time. No cost or credentials appear on labels.

## Finding and selecting products

Use **Shopify product type**, **Shopify collection** or **Vendor** to filter the imported catalog before any local retail categories have been assigned. **Retail category (manual)** is a separate field; an unmapped bicycle is not silently classified into the Bicycle category. The category selector shows counts, includes Unmapped, and disables unused categories unless retaining an already applied filter. Metadata counts describe the whole imported shop catalog, while result counts apply all selected filters together.

Type a product name, size, colour, SKU or barcode. Whitespace is trimmed and every search word must match somewhere across product/variant names, identifiers, Shopify type or vendor; for example `bicycle 16` can match a bicycle title and a 16-inch variant. Literal `%`/`_` characters are escaped. Exact barcode/SKU matches sort before partial matches. Dropdown changes apply automatically; typed searches debounce briefly, and Enter/Search applies immediately. Newer searches replace stale requests rather than showing old results.

**Clear filters** resets the search and dropdowns. It does not discard checked products or change a frozen selection. **Clear selection** is separate. You can check products across searches/pages, then Use selected variants, or Select all filtered results. Bulk selection and CSV export use the filters that produced the displayed results; selection waits while a newer search is pending. The identifier-allocation panel is collapsed by default so it does not obstruct the product list.

Collection filtering uses only imported memberships (currently the first five per product); the UI warns when cached collection coverage is incomplete. Filters and dropdown metadata remain scoped to the configured shop. `GET /crm/retail/api/filter-options` returns only metadata/counts, not the full product list. Product listing, CSV and selection share `q`, `product_type`, `vendor`, `collection`, `category`, `issue` and `active` filter semantics. No categories, SKUs, barcodes or Shopify data are changed by searching.

## Identity and audit rules

- Primary external identity is `(shop, Shopify variant ID)`, with an immutable local integer ID. Titles, SKU and barcode are never identity keys.
- `sku`/`barcode` contain imported Shopify values. `local_sku`/`local_barcode` contain separately allocated values. Local values are always shown as pending synchronization; a matching imported value does not falsely certify a write. No product write endpoint exists.
- Imported duplicates remain importable. Barcode conflicts block ordinary label generation. Allocation skips existing nonempty SKUs/barcodes by default; changing the scan identifier requires a documented local alternative. Shopify values remain unchanged.
- Missing/empty, invalid, duplicate, documented shared, local pending, multiple variants, missing options, missing-in-Shopify and stale states remain distinct. Missing SKU/barcode audit counts refer to the Shopify fields; a local assignment is reported separately.
- External identifiers are not forced into a fixed numeric length or internal prefix. Printable ASCII values up to 100 characters can be printed as Code 128 without changing their contents. External symbology/GS1 issuance is unknown; the application does not certify manufacturer GTIN ownership or issue GTINs. Unsupported Code 128 values remain preserved and flagged for review.
- Duplicate-group exceptions require the entire exact group, a reason, explicit confirmation and `retail.products.manage`. Approvals can be revoked by appending an audited revocation; prior records are never deleted. Review already downloaded/printed labels separately after a revocation. Barcode-field sharing and effective scan sharing are separate approvals. Review the operational ambiguity before approving shared scan values; this phase provides no POS lookup behavior.
- A local barcode that conflicts with a newly imported Shopify barcode is marked for reconciliation. A manager can approve it as the intentional local alternative, with a reason; no identifier is overwritten. Collisions with another variant still block ordinary printing.
- New SKU allocation uses an explicitly selected category, e.g. `PP-BIC-000001`, `PP-GFT-000002`, `PP-OTH-000003`. Categories are not inferred from titles. Existing SKUs are preserved.
- New internal barcodes use `P` plus an uppercase base-36 sequence, starting `P1`, `P2`, … `PZ`, `P10`. This is a company-internal namespace, not EAN/GTIN/GS1. Short identifiers help thermal media; committed identifiers are never reformatted to fit labels.
- Independent database-backed counters serialize allocation per shop. Unique constraints on the allocation ledger prevent collisions. Every candidate is checked against imported and previously committed values. Previewing/canceling does not allocate or reserve a value. If products/counters change, confirmation returns a conflict and requires a new preview. Repeated confirmation of a committed preview returns its prior result.
- Committed identifier, audit and sharing-exception records are append-only, protected by SQLite/PostgreSQL triggers. No application endpoint updates/deletes them. Counters and tombstones must be included in backups.

## Read-only Shopify synchronization

The importer inherits the existing Shopify client’s authentication and uses one fixed GraphQL **query** document. It reads `shop.currencyCode` and paginated `productVariants`, product metadata, inventory-item identity/tracking and bounded collections. It does not send product mutations or subscribe to/alter Shopify webhooks. Existing Meta webhook behavior is unchanged.

Required Shopify app scopes: **read_products** and **read_inventory** for the inventory-item fields. Reuse existing server-only `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` and API version. No token is sent to the browser or persisted in the retail tables. Authentication still uses the existing OAuth token endpoint; read-only refers to product data, not the HTTP verb used for GraphQL/OAuth.

Full imports use cursor pagination with **50 variants per page**, bounded to the configured catalog limit. Collections include the first **5 per product**, with an explicit completeness flag. This bounds GraphQL requested cost; it is not represented as a complete collection history. Bulk operations were not selected for Phase 1: the tested 1,500-variant catalog takes 30 bounded read pages, needs no remote bulk-operation mutation or signed-JSONL downloader, and can be staged/checkpointed with the existing worker convention.

Each successful page is staged with its cursor/heartbeat. The live local master changes only when all pages succeed, in one publishing transaction. A failed read or invalid response leaves the master unchanged and retains staged pages and failure history. Start a new job to retry; unfinished staging is never misrepresented as published master data. Repeated imports update by variant ID without losing local identifiers.

Incremental reads use the previous successful job's start watermark with a five-minute overlap. A full import marks absent old variants missing/stale without deleting them. Incremental jobs do not establish deletion; run periodic full imports for reconciliation. Updates newer than the import start are marked stale for another pass. An empty full replacement of a nonempty master fails for review rather than silently marking everything missing. Pagination is an eventually consistent Shopify view, not a remote point-in-time snapshot; use a final full reconciliation before large label allocations.

Read retries are bounded (four attempts), respect a short `Retry-After`, apply exponential delays for transient failures and account for GraphQL throttle capacity. Long rate-limit waits fail safely for a later manual retry. Errors in job history are fixed, sanitized messages, never raw Shopify response bodies. Only one queued/running import can own a shop. Abandoned running jobs expire after 15 minutes without a heartbeat and do not auto-publish.

The worker processes **explicitly queued** jobs only; this release does not schedule unattended daily imports. No live Shopify request was made during development/tests.

## Printer/PDF architecture

`retail_labels.Label` is an output-neutral label value object. `LabelBackend.render()` is the backend boundary. `PDFBackend` is the only implemented backend. A future TSPL backend can use the same retained label/job data without changing barcode allocation or the product master. Raw USB, print agents, ESC/POS receipts and automatic OS spooler operations are absent.

Code 128 is generated by `python-barcode`; fpdf2 renders actual vector bars and embeds Noto fonts, with HarfBuzz shaping for Sinhala. No AI-generated symbol or barcode font approximations are used. The symbol includes its Code 128 checksum. Human-readable values are not truncated; non-fitting symbols/values are rejected. Product-name lines can be shortened, while SKU and price visibility are configurable.

Supported presets: **25 × 15, 30 × 15, 40 × 20, 50 × 30 and 60 × 40 mm**. A custom profile can set physical size, margins, whole-dot module width, barcode height, text size, name limit, brand/variant/SKU/price visibility, copies, labels per row, roll/A4 layout and calibration offsets. The scanner-safe baseline is **203 dpi**, minimum two-dot modules (25.4/203 mm per dot) and ten-module horizontal quiet zones. Bar edges align to the dot grid. Labels that cannot accommodate the existing identifier are blocked; use a larger label instead of shrinking it unsafely.

Compact 15 mm labels combine product/variant text and hide brand/SKU/price by default. Their ability to encode short test value `P1` does not mean a long manufacturer identifier will fit. The generic A4 mode uses a top-left grid, configurable row count/vertical gaps and physical labels; it is not a catalog of pre-cut commercial A4 sheet offsets.

**Gap, sensor calibration and density are stored operator settings.** The PDF/Chrome/driver path cannot set the printer's firmware density or calibrate its sensor. Apply the settings manually in the DBL Windows driver where supported. Density uses a stored generic 0–15 value; verify the actual driver scale. Confirm usable print width, minimum label width and roll support against the purchased unit; no maximum DBL printhead width is assumed by this implementation.

### 30x15 Compact — SKU & Price

Choose **30x15 Compact — SKU & Price** in the Label profile dropdown (API preset ID `30x15-compact-sku-price`). This is a separate preset; existing 30×15 and other saved profiles retain their layouts.

It prints exactly: one product-name line (5 pt), a Code 128 graphic (5 mm high), `barcode | SKU: value` together on one line (4.5 pt), then the variant selling price in `Rs. 1,500.00` format on a separate bottom line (5 pt). Brand and variant lines are hidden. The default margin is 0.625 mm, adjustable between 0.5 and 0.75 mm subject to physical printing validation. Modules remain at least two 203-dpi dots with ten-module quiet zones.

Product names may be shortened with an ellipsis without splitting combining marks/conjuncts. Barcode values, SKUs and prices are never shortened or scaled down. An SKU and a valid LKR variant selling price are required. SKU priority remains the existing Shopify SKU, then an assigned local SKU if Shopify has none. The price comes from the selected variant's selling price, not compare-at price or another variant; missing/non-LKR currency is rejected rather than assumed. All selected rows are checked for fit before preview/queueing, including rows beyond the five-label preview.

This layout's size, font sizes, barcode height and field visibility are fixed; use another preset if the required identifiers cannot fit. Margins, copy/layout settings and calibration can be saved as a custom profile while retaining this layout. No migration, allocation or Shopify edit is required. Test PDF: [compact SKU/price sample](samples/dbl365b-30x15-compact-sku-price-test.pdf). No physical printer certification is implied.

See [DBL 365B setup and hardware acceptance](dbl365b-setup.md).

## Print operations

1. Search/filter products, check variants across pages or choose **Select all filtered results**. The server freezes the selected IDs, scoped to the user and shop, for two hours. Later filter changes do not add products to that selection.
2. Choose **Use selected variants**. To assign missing values, choose SKU/barcode, the explicit SKU category if applicable, preview every proposed assignment using the paginated preview and confirm. A barcode alternative needs a reason.
3. Choose **Print Selected**, then a preset or saved custom profile. Copy overrides are edited on paginated selected rows; the default applies to the rest. SKU allocation requires an explicit category choice, not a default inferred category.
4. Save changed settings as a named profile, preview the first five labels, then confirm **Generate PDF batch**. The server binds generation to the reviewed preview for ten minutes and rejects changes to product data, profile settings or copy counts until a fresh preview is created.
5. The worker renders and stores a private PDF with a SHA-256 integrity check. Download it and print through Chrome/Windows. Generation and download do not imply a physical print.
6. Only after inspecting physical labels, choose **Confirm physically printed**. Reprint creates a new audited job from the retained snapshot, linked to its parent. Unsafe/changed current scan identities block downloads and reprinting until review. Already downloaded or physically printed labels cannot be recalled by software.

Limits: 10,000 selected variants, 1–1,000 copies per variant, configurable total labels (default 5,000; hard cap 10,000), 32 MiB generated PDF and catalog safety cap (default 50,000 variants). One job is processed per worker cycle. Master list and history responses are paginated; CSV is streamed. The 1,500-label test renders a real PDF, not a stub. PDF generation uses bounded input; set a lower per-job limit on small Railway instances.

Failed PDF jobs retain their rows/profile for review and explicit reprinting. Missing/damaged files fail integrity checks; they are never substituted with unrelated documents. Store the directory on persistent storage and back it up. No confidential costs, payment data or tokens are included in label snapshots.

## Permissions

Administrators have all Phase 1 capabilities when the feature is enabled. Legacy staff roles do **not** automatically inherit retail access. Use the existing **Staff & Access** checkboxes; granular grant names are stored in the existing `section_access` list:

| Grant | Capability / companion grants |
|---|---|
| `retail.products.view` | Product search, master, barcode list, selection and history |
| `retail.products.audit` | Audit/CSV; grant products.view for detailed UI drill-down |
| `retail.products.manage` | Category mapping and documented exceptions/alternatives; normally paired with products.view |
| `retail.barcodes.assign` | Allocation preview/commit; pair with products.view for selection/UI |
| `retail.barcodes.print` | Profiles, PDFs, jobs/downloads/reprints; pair with products.view for selection/UI |
| `retail.sync.run` | Explicitly queue a read-only import; pair with sync.view |
| `retail.sync.view` | Import history/error reports |
| `retail.settings.manage` | Save label profiles; pair with barcodes.print for the settings UI |

Suggested manager: products.view/audit/manage, barcodes.assign/print, sync.view, settings.manage; add sync.run only if desired. Cashier/warehouse: products.view and barcodes.print only when authorized. Permissions are checked on every endpoint, not inferred from hidden controls. Action-only grants do not become default landing pages. All data queries and references use the configured server-side shop, not a caller-selected tenant. Selections/previews are additionally bound to the creating user. POST changes require CSRF and explicit confirmation. CSV formula prefixes are escaped.

## Tables and APIs

New tables: `retail_shops`, `retail_variants`, `retail_sync_jobs`, `retail_import_rows`, `retail_counters`, `retail_identifiers`, `retail_previews`, `retail_audits`, `retail_exceptions`, `retail_label_templates`, `retail_print_jobs`.

UI: `/crm/retail/products`, `/barcodes`, `/labels`, `/audit`, `/sync` under the same prefix.

API prefix `/crm/retail/api`:

- `GET products`, `GET filter-options`, `GET products/{id}/history`, `GET audit`, `GET export.csv`
- `POST selection`, `GET selection/{token}` (paginated)
- `POST allocations/preview`, `POST allocations/commit`
- `GET allocations/{token}` (paginated preview), `POST products/{id}/category`, `POST products/{id}/approve-alternative`, `POST exceptions`, `POST exceptions/revoke`
- `POST sync`, `GET sync`, `GET sync/{id}/errors.csv`
- `GET profiles`, `POST profiles`
- `POST labels/preview`, `POST labels/jobs`, `GET labels/jobs`
- `GET labels/jobs/{id}/pdf`, `POST labels/jobs/{id}/printed`, `POST labels/jobs/{id}/reprint`

`POST labels/preview` returns `X-Retail-Preview`; include that token as `preview_token` when confirming `POST labels/jobs`. This pins the intended document data and configuration.

No Shopify product-write endpoint is included. `SHOPIFY_PRODUCT_WRITE_ENABLED=false` is reserved; setting it true does not add an implementation.

## Deployment and initial import

1. Review and merge the feature branch. No automatic deployment is performed by development.
2. Back up the production database, current application revision and persistent volume. Record `SHOW server_version;` and `SELECT version_num FROM alembic_version;` in a private deployment record. PostgreSQL **16** and local SQLite are tested; actual Railway version was not accessed. Validate on a restored staging copy if different or if the current migration head is not the expected predecessor `20261111_41`.
3. Install the pinned runtime requirements. The fonts and their license are bundled under `static/retail-fonts`. Offline decoder/rasterizer packages are test-only in `requirements.retail-test.txt`.
4. Keep `RETAIL_ENABLED=false` and `SHOPIFY_PRODUCT_WRITE_ENABLED=false`. Run `python -m alembic upgrade head` using the normal application environment. Normal app startup also performs the project's existing create-all/upgrade sequence. Migration `20261117_42` is additive and repeatable; it adds no catalog rows and calls no external service.
5. Configure `RETAIL_LABEL_DIR=/data/retail-labels` on the existing persistent `/data` volume. Ensure the process can create mode-0700 directories and mode-0600 files. Existing deployment configuration and other volumes must be preserved.
6. Verify existing Shopify credentials and the required read scopes. Leave existing API version and order-sync flags as configured. Do not request product write scopes for Phase 1.
7. Set `RETAIL_MAX_LABELS` (default 5000) and `RETAIL_MAX_VARIANTS` (default 50000) if needed. Leave these unset for defaults. Enable `RETAIL_ENABLED=true` only after migration/staging checks. Deploy web/worker code together; the retail worker is a separate thread and starts only while enabled. This guide does not change production variables automatically.
8. Grant staff permissions, open **Import & Sync History**, confirm **Full catalog import**, and monitor completion. No labels or identifiers are automatically allocated during import.
9. Review Product Audit and download CSV before allocating. Resolve duplicates/options and explicitly map ambiguous categories. This audit is the dry run: it performs no Shopify repair.
10. Allocate/preview a few missing local values, test PDFs, complete the hardware checks below, then gradually raise batch sizes. Run a final full reconciliation before broad allocation and repeat full imports periodically for deletion detection.

## Rollback and recovery

- Disable `RETAIL_ENABLED` to hide navigation and stop new retail processing on restart; other CRM features keep their existing flags. Roll back application code if needed, while retaining the new tables, counters, audit records and PDFs. Do **not** delete/reset committed sequences or run destructive SQL.
- The downgrade intentionally retains the schema and immutable records. Prefer keeping Alembic at the additive head rather than decrementing its version record. No legacy schema rollback is required.
- Running read jobs/PDF generation interrupted by a restart become failed after 15 minutes of stale heartbeat/claim time when the feature is enabled again. Inspect history, then queue a new import or explicit reprint. Never manually label a partial staging job complete.
- A failed sync's published master remains unchanged. If an apparently complete import has stale/missing results, inspect audit flags and start a fresh full import after confirming credentials/scopes. No automatic mass repair exists.
- Back up database and volume together. Restore counters/identifier tombstones with master rows to avoid reuse. Retain immutable audits and committed identifiers indefinitely. Staging rows and generated PDFs consume storage per job; any retention cleanup should be reviewed, backed up and preserve job metadata/snapshots and all identifier/audit records. No automatic cleanup/deletion is introduced.

## Verification and remaining hardware work

Automated coverage includes fake Shopify pagination/rate limits, new/update/repeated/interrupted imports, missing/duplicate identifiers, archived/deleted variants, local conflict preservation, counter races and idempotency, append-only audit migration, permissions/CSRF/cross-shop isolation, CSV injection, actual PDF dimensions, numeric/alphanumeric Code 128 decoder round trips at 203 dpi, Sinhala/English text, copy counts, A4, a real 1,500-variant/label job, failed PDF generation/reprints and Chromium frontend flows at desktop/tablet sizes.

Commands and final counts are recorded in the implementation report. All tests use local/disposable data and mocked external transports. There were no live Shopify operations, customer messages, production migrations or physical printer calls.

Samples in `docs/samples` contain non-production identifiers. Digital decoding demonstrates valid generated symbols, not physical certification. Remaining work: confirm the purchased printer's usable dimensions/driver behavior, calibrate each media size, test real scanner accuracy/quiet zones/adhesion, and validate the first authorized read-only import on staging/production. TSPL direct printing, ESC/POS receipts, POS checkout, warehouse stock movements and retail payment processing remain outside Phase 1.
