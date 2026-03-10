# inventree-barcode-lookup

An [InvenTree](https://inventree.org/) plugin that resolves retail UPC/EAN barcodes against external product databases. Scan a barcode from a store-bought product and InvenTree will look it up, optionally create a Part, and add stock at a location of your choice.

## Features

- **Retail barcode detection** — recognizes UPC-A (12-digit), EAN-13, and EAN-8 formats with check digit validation
- **External product lookup** — queries [UPCitemdb](https://www.upcitemdb.com/) and [Open Food Facts](https://world.openfoodfacts.org/) in a waterfall pattern
- **Auto-create Parts** — optionally creates a new Part with product name, description, brand, category, and image
- **Scan-to-stock with location selection** — browse the location tree and add stock to a specific location in one step
- **Last-used location memory** — remembers the last location each user scanned into, per user
- **Works with the InvenTree app** — the Android/iOS app's barcode scanner works immediately with no changes
- **Barcode assignment** — assigns the scanned barcode to the Part so future scans are instant (no external lookup needed)
- **Configurable** — toggle auto-creation, auto-stock, default location/quantity, name format, image downloads

## How It Works

### Standard Scan Flow (InvenTree App)

When you scan a barcode from the InvenTree app, a USB scanner, or any client hitting `/api/barcode/`:

1. InvenTree's barcode API passes the scan to all registered barcode plugins
2. This plugin checks if the barcode is a valid retail format (UPC-A, EAN-13, EAN-8)
3. If the barcode is already assigned to a Part, it returns the match immediately
4. Otherwise, it queries external product databases for product information
5. If **Auto-Create Parts** is enabled, it creates a new Part and assigns the barcode
6. If **Auto-Add Stock** is also enabled, it creates a StockItem at the **Default Location**
7. If auto-create is disabled, it returns nothing (the app shows "barcode not found")

### Scan-to-Stock Flow (Custom API)

For interactive location selection and explicit stock creation, use the plugin's custom endpoints:

1. Browse locations via `GET /plugin/retail-barcode/api/locations/?parent=0`
2. Drill into sublocations by passing `?parent=<id>`
3. Scan to stock via `POST /plugin/retail-barcode/api/scan-to-stock/`
4. The selected location is remembered for next time

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
| **Auto-Add Stock on Scan** | Off | Also create a StockItem when auto-creating a Part via standard barcode scan |
| **Default Stock Location** | 0 (none) | Location ID for auto-added stock and the starting default for scan-to-stock |
| **Default Quantity** | 1 | Quantity for auto-added stock items |

## API Endpoints

All endpoints require InvenTree authentication (token, session, or basic auth).

### Browse Locations

```
GET /plugin/retail-barcode/api/locations/?parent=0
```

Returns locations at the given tree level. Omit `parent` or set to `0` for top-level locations.

**Response:**
```json
{
    "locations": [
        {
            "id": 1,
            "name": "Warehouse",
            "description": "Main warehouse",
            "pathstring": "Warehouse",
            "parent_id": null,
            "has_children": true,
            "child_count": 3,
            "items_count": 12,
            "location_type": "Building"
        }
    ],
    "current": null,
    "breadcrumbs": []
}
```

Drill into sublocations:
```
GET /plugin/retail-barcode/api/locations/?parent=1
```

The response includes `breadcrumbs` (path from root) for navigation back up the tree.

### Scan to Stock

```
POST /plugin/retail-barcode/api/scan-to-stock/
Content-Type: application/json

{
    "barcode": "049000042566",
    "location_id": 5,
    "quantity": 2
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `barcode` | Yes | — | UPC-A, EAN-13, or EAN-8 barcode |
| `location_id` | No | Last-used or plugin default | Stock location ID |
| `quantity` | No | Plugin default (1) | Quantity to add |

**Response:**
```json
{
    "success": true,
    "part": {
        "pk": 42,
        "name": "Coca-Cola Classic 12oz",
        "description": "...",
        "created": true
    },
    "stock_item": {
        "pk": 100,
        "quantity": 2,
        "location": {
            "pk": 5,
            "name": "Pantry Shelf A",
            "pathstring": "Home/Kitchen/Pantry Shelf A"
        }
    }
}
```

This endpoint always creates Parts for recognized barcodes (regardless of the Auto-Create setting), because the user is explicitly requesting stock creation. It also saves the location as the user's last-used location.

### Last-Used Location

```
GET  /plugin/retail-barcode/api/last-location/    # Get last-used location
PUT  /plugin/retail-barcode/api/last-location/     # Set: {"location_id": 5}
DELETE /plugin/retail-barcode/api/last-location/   # Clear last-used location
```

Returns the user's last-used location (or the plugin default if none set). The scan-to-stock endpoint updates this automatically.

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
- **Last-used location uses Django's cache.** If the cache is cleared (server restart with in-memory cache), the user's last location resets to the plugin default. This is a non-issue with persistent cache backends (Redis, Memcached, file).

## Requirements

- InvenTree >= 0.13.0
- Python >= 3.9

## License

MIT
