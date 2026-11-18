# Non-production Code 128 samples

- `dbl365b-25x15-test.pdf`
- `dbl365b-30x15-test.pdf`
- [30x15 Compact — SKU & Price test](dbl365b-30x15-compact-sku-price-test.pdf)
- `dbl365b-40x20-test.pdf`
- `dbl365b-50x30-test.pdf`
- `dbl365b-60x40-test.pdf`
- `code128-a4-test.pdf`

Roll samples encode exact value `P1`; A4 samples encode `PP-BIC-000123` and include English/Sinhala text. All product names, SKU and price values are test data. These files do not allocate a production identifier. Digital roll samples were rasterized at 203 dpi and independently decoded by ZXing. Printed DBL 365B/scanner acceptance remains required; see `../dbl365b-setup.md`.

The separate compact SKU/price sample uses the test example “Magical Kids Water Book”, `P2 | SKU: 1001150`, and `Rs. 1,500.00`. It has a 30×15 mm page, 5/4.5/5 pt text, a 5 mm Code 128 symbol and 0.625 mm margins. The barcode was independently decoded at 203 dpi. These are test values and do not allocate or change identifiers.
