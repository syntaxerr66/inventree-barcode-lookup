# inventree-barcode-lookup

An [InvenTree](https://inventree.org/) plugin that resolves retail UPC/EAN barcodes against external product databases. Scan a barcode from a store-bought product and InvenTree will look it up and optionally create a Part automatically.

## Features

- **Retail barcode detection** — recognizes UPC-A (12-digit), EAN-13, and EAN-8 formats with check digit validation
- **External product lookup** — queries [UPCitemdb](https://www.upcitemdb.com/) and [Open Food Facts](https://world.openfoodfacts.org/) in a waterfall pattern
- **Auto-create Parts** — optionally creates a new Part with product name, description, brand, category, and image
- **Works with the InvenTree app** — the Android/iOS app's barcode scanner works immediately with no changes
- **Barcode assignment** — assigns the scanned barcode to the Part so future scans are instant (no external lookup needed)
- **Configurable** — toggle auto-creation, set a default category, choose name format, enable/disable image downloads

## How It Works

When you scan a barcode (from the InvenTree app, a USB scanner, or any API client):

1. InvenTree's barcode API (`/api/barcode/`) passes the scan to all registered barcode plugins
2. This plugin checks if the barcode is a valid retail format (UPC-A, EAN-13, EAN-8)
3. If the barcode is already assigned to a Part, it returns the match immediately
4. Otherwise, it queries external product databases for product information
5. If **Auto-Create Parts** is enabled, it creates a new Part and assigns the barcode
6. If auto-create is disabled, it returns nothing (the app shows "barcode not found")

## Installation

```bash
pip install inventree-barcode-lookup
```

Or install from source:

```bash
pip install git+https://github.com/syntaxerr66/inventree-barcode-lookup.git
```

Then restart InvenTree and enable the plugin in **Settings → Plugins**.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| **Auto-Create Parts** | Off | Create a new Part when a scanned barcode is found in an external database but doesn't match an existing Part |
| **Default Category** | 0 (none) | Part category ID to assign to auto-created parts |
| **Set Product Image** | On | Download and set the product image from the lookup result |
| **Part Name Format** | Brand + Name | `brand_name` includes brand (e.g., "WD-40 Multi-Use Product"), `name_only` uses just the product name |

## Data Sources

| Source | Coverage | Rate Limit | Auth Required |
|--------|----------|------------|---------------|
| [UPCitemdb](https://www.upcitemdb.com/) | 687M+ products, all categories | 100 req/day (free tier) | No |
| [Open Food Facts](https://world.openfoodfacts.org/) | 4M+ food products, 150 countries | Unlimited | No |

The plugin tries UPCitemdb first (broader coverage), then falls back to Open Food Facts. No API keys are required for the free tiers.

## Limitations

- **100 lookups/day** on the free UPCitemdb tier. After that, only Open Food Facts (food items) will work until the next day. This is plenty for personal/hobby use.
- **Not all products have barcodes in these databases.** Specialty items, raw components, and non-retail products may not be found.
- **Auto-created parts are basic.** They get a name, description, and image, but you'll likely want to organize them into proper categories and add parameters manually.

## Requirements

- InvenTree >= 0.13.0
- Python >= 3.9

## License

MIT
