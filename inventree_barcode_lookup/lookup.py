"""Product lookup via external barcode databases.

Implements a waterfall strategy:
1. UPCitemdb (free tier, 100 req/day, broad coverage)
2. Open Food Facts (unlimited, food/grocery products)

Returns a normalized ProductInfo dict or None.
"""

import logging

import requests

from . import __version__

logger = logging.getLogger('inventree')

# Shared timeout for all external API calls (seconds)
REQUEST_TIMEOUT = 10


class ProductInfo:
    """Normalized product data from any lookup source."""

    __slots__ = ('name', 'description', 'brand', 'category', 'image_url', 'source')

    def __init__(self, *, name, description='', brand='', category='',
                 image_url='', source=''):
        self.name = name
        self.description = description
        self.brand = brand
        self.category = category
        self.image_url = image_url
        self.source = source

    def full_name(self) -> str:
        """Combine brand + name for the Part name field."""
        if self.brand and self.brand.lower() not in self.name.lower():
            return f'{self.brand} {self.name}'
        return self.name

    def __repr__(self):
        return f'ProductInfo(name={self.name!r}, brand={self.brand!r}, source={self.source!r})'


def lookup_upcitemdb(barcode: str) -> ProductInfo | None:
    """Query UPCitemdb free trial endpoint.

    Docs: https://www.upcitemdb.com/wp/docs/main/development/responses/
    Free tier: 100 lookups/day, no API key required.
    """
    url = 'https://api.upcitemdb.com/prod/trial/lookup'
    params = {'upc': barcode}

    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning('UPCitemdb request failed: %s', exc)
        return None

    if resp.status_code != 200:
        logger.debug('UPCitemdb returned HTTP %s for barcode %s', resp.status_code, barcode)
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    items = data.get('items')
    if not items:
        return None

    item = items[0]
    title = item.get('title', '').strip()
    if not title:
        return None

    # Pick the first available image from the images list
    images = item.get('images') or []
    image_url = images[0] if images else ''

    return ProductInfo(
        name=title,
        description=item.get('description', '').strip(),
        brand=item.get('brand', '').strip(),
        category=item.get('category', '').strip(),
        image_url=image_url,
        source='upcitemdb',
    )


def lookup_openfoodfacts(barcode: str) -> ProductInfo | None:
    """Query Open Food Facts API v2.

    Docs: https://openfoodfacts.github.io/openfoodfacts-server/api/
    No auth, no rate limit, focused on food/grocery products.
    """
    url = f'https://world.openfoodfacts.org/api/v2/product/{barcode}'
    headers = {'User-Agent': f'InvenTreeBarcodePlugin/{__version__} (https://github.com/syntaxerr66/inventree-barcode-lookup)'}

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning('Open Food Facts request failed: %s', exc)
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    if data.get('status') != 1:
        return None

    product = data.get('product', {})
    name = product.get('product_name', '').strip()
    if not name:
        return None

    return ProductInfo(
        name=name,
        description=product.get('generic_name', '').strip(),
        brand=product.get('brands', '').strip(),
        category=(product.get('categories_tags') or [''])[0].replace('en:', ''),
        image_url=product.get('image_front_url', ''),
        source='openfoodfacts',
    )


def lookup_product(barcode: str) -> ProductInfo | None:
    """Waterfall lookup across all configured sources.

    Tries each source in order, returns the first successful result.
    """
    for lookup_fn in (lookup_upcitemdb, lookup_openfoodfacts):
        result = lookup_fn(barcode)
        if result is not None:
            return result

    return None
