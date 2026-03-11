"""InvenTree plugin that resolves retail UPC/EAN barcodes via external product databases."""

import logging

from plugin import InvenTreePlugin
from plugin.mixins import BarcodeMixin, SettingsMixin, UrlsMixin

from .lookup import lookup_product
from .validators import is_retail_barcode

logger = logging.getLogger('inventree')


class RetailBarcodePlugin(BarcodeMixin, SettingsMixin, UrlsMixin, InvenTreePlugin):
    """Resolve retail UPC/EAN barcodes against external product databases.

    When a barcode is scanned:
    1. Check if it's a valid UPC-A, EAN-13, or EAN-8 format (with check digit validation).
    2. Check if it's already assigned to a Part in the database.
    3. Look up the barcode in external databases (UPCitemdb, Open Food Facts).
    4. Optionally auto-create a Part from the lookup result.
    5. Optionally create a StockItem at the default location.

    Also exposes custom API endpoints for interactive location selection
    and scan-to-stock operations.
    """

    NAME = 'RetailBarcodePlugin'
    SLUG = 'retail-barcode'
    TITLE = 'Retail Barcode Lookup'
    DESCRIPTION = 'Resolve retail UPC/EAN barcodes against product databases and optionally auto-create parts'
    VERSION = '0.3.0'
    AUTHOR = 'syntaxerr66'

    SETTINGS = {
        'AUTO_CREATE_PARTS': {
            'name': 'Auto-Create Parts',
            'description': (
                'Automatically create a new Part when a scanned retail barcode '
                'is found in an external product database but does not match '
                'any existing Part. If disabled, unrecognized barcodes return '
                '"not found".'
            ),
            'validator': bool,
            'default': False,
        },
        'CREATE_UNKNOWN': {
            'name': 'Create Unknown Products',
            'description': (
                'When a scanned barcode is a valid retail format (UPC/EAN) but '
                'is not found in any external product database, still create a '
                'placeholder Part with the barcode as the name. You can edit '
                'the Part details later. Requires Auto-Create Parts to be enabled.'
            ),
            'validator': bool,
            'default': True,
        },
        'DEFAULT_CATEGORY': {
            'name': 'Default Category',
            'description': (
                'Part category ID to assign to auto-created parts. '
                'Leave as 0 to create parts without a category.'
            ),
            'default': 0,
        },
        'SET_IMAGE': {
            'name': 'Set Product Image',
            'description': (
                'Download and set the product image from the lookup result '
                'on auto-created parts.'
            ),
            'validator': bool,
            'default': True,
        },
        'NAME_FORMAT': {
            'name': 'Part Name Format',
            'description': (
                'How to format the Part name. '
                '"brand_name" includes the brand (e.g., "Tide Laundry Detergent"). '
                '"name_only" uses just the product name.'
            ),
            'choices': [
                ('brand_name', 'Brand + Product Name'),
                ('name_only', 'Product Name Only'),
            ],
            'default': 'brand_name',
        },
        'AUTO_ADD_STOCK': {
            'name': 'Auto-Add Stock on Scan',
            'description': (
                'When auto-creating a Part from a standard barcode scan, '
                'also create a StockItem at the default location. '
                'Requires Auto-Create Parts to be enabled.'
            ),
            'validator': bool,
            'default': False,
        },
        'DEFAULT_LOCATION': {
            'name': 'Default Stock Location',
            'description': (
                'Stock location ID used when auto-adding stock from a standard '
                'barcode scan, and as the initial default for the scan-to-stock '
                'endpoint. Leave as 0 for no default.'
            ),
            'default': 0,
        },
        'DEFAULT_QUANTITY': {
            'name': 'Default Quantity',
            'description': 'Default quantity when auto-adding stock.',
            'default': 1,
        },
    }

    # ── URL registration ──────────────────────────────────────────────

    def setup_urls(self):
        from django.urls import path

        from .views import LastLocationView, LocationTreeView, ScanToStockView

        return [
            path('api/locations/', LocationTreeView.as_view(), name='retail-barcode-locations'),
            path('api/scan-to-stock/', ScanToStockView.as_view(), name='retail-barcode-scan-to-stock'),
            path('api/last-location/', LastLocationView.as_view(), name='retail-barcode-last-location'),
        ]

    # ── Standard barcode scan (BarcodeMixin) ──────────────────────────

    def scan(self, barcode_data):
        """Handle a barcode scan event.

        Called by InvenTree's barcode API when a barcode is scanned.
        Returns a dict with the matched Part data, or None to pass
        to the next barcode plugin.
        """
        if not isinstance(barcode_data, str):
            return None

        barcode_data = barcode_data.strip()

        if not is_retail_barcode(barcode_data):
            return None

        logger.info('Retail barcode detected: %s', barcode_data)

        # Check if already assigned to a Part
        existing = self._find_existing_part(barcode_data)
        if existing is not None:
            logger.info('Barcode %s matched existing Part pk=%s', barcode_data, existing.pk)
            return {
                'part': existing.format_matched_response(),
            }

        # Look up in external databases
        product = lookup_product(barcode_data)

        if not self.get_setting('AUTO_CREATE_PARTS'):
            if product is not None:
                logger.info(
                    'Auto-create disabled. Found "%s" for barcode %s but not creating Part.',
                    product.full_name(), barcode_data,
                )
            else:
                logger.info('Barcode %s not found and auto-create disabled.', barcode_data)
            return None

        if product is not None:
            logger.info('Barcode %s resolved to: %s', barcode_data, product)
            part = self._create_part(barcode_data, product)
        elif self.get_setting('CREATE_UNKNOWN'):
            logger.info('Barcode %s not found externally; creating placeholder Part.', barcode_data)
            part = self._create_placeholder_part(barcode_data)
        else:
            logger.info('Barcode %s not found and CREATE_UNKNOWN disabled.', barcode_data)
            return None

        if part is None:
            return None

        # Optionally add stock at the default location
        if self.get_setting('AUTO_ADD_STOCK'):
            location_id = self._resolve_setting_int('DEFAULT_LOCATION')
            quantity = self._resolve_setting_int('DEFAULT_QUANTITY') or 1
            if location_id and location_id > 0:
                self._create_stock_item(part, location_id, quantity)
            else:
                logger.info('AUTO_ADD_STOCK enabled but no DEFAULT_LOCATION set; skipping stock creation.')

        return {
            'part': part.format_matched_response(),
        }

    # ── Shared helpers (used by both scan() and views) ────────────────

    def _find_existing_part(self, barcode_data: str):
        """Check if any Part already has this barcode assigned."""
        from InvenTree.helpers import hash_barcode
        from part.models import Part

        barcode_hash = hash_barcode(barcode_data)
        return Part.lookup_barcode(barcode_hash)

    def find_or_create_part(self, barcode_data: str):
        """Find an existing Part by barcode, or create one from external lookup.

        Returns (part, created) tuple. part is None if the barcode format
        is invalid. When the external lookup fails, creates a placeholder
        Part so the barcode is always captured.
        """
        barcode_data = barcode_data.strip()

        if not is_retail_barcode(barcode_data):
            return None, False

        existing = self._find_existing_part(barcode_data)
        if existing is not None:
            return existing, False

        product = lookup_product(barcode_data)
        if product is not None:
            part = self._create_part(barcode_data, product)
        else:
            part = self._create_placeholder_part(barcode_data)

        return part, (part is not None)

    def _create_part(self, barcode_data: str, product):
        """Create a new Part from external product lookup data."""
        from part.models import Part, PartCategory

        name_format = self.get_setting('NAME_FORMAT')
        if name_format == 'brand_name':
            name = product.full_name()
        else:
            name = product.name

        name = name[:100]

        desc_parts = []
        if product.description:
            desc_parts.append(product.description)
        if product.category:
            desc_parts.append(f'Category: {product.category}')
        desc_parts.append(f'Barcode: {barcode_data}')
        if product.source:
            desc_parts.append(f'Source: {product.source}')
        description = ' | '.join(desc_parts)[:250]

        category = None
        cat_id = self._resolve_setting_int('DEFAULT_CATEGORY')
        if cat_id and cat_id > 0:
            category = PartCategory.objects.filter(pk=cat_id).first()

        try:
            part = Part.objects.create(
                name=name,
                description=description,
                category=category,
                component=False,
                purchaseable=True,
                active=True,
            )
        except Exception as exc:
            logger.error('Failed to create Part for barcode %s: %s', barcode_data, exc)
            return None

        try:
            part.assign_barcode(barcode_data=barcode_data, raise_error=False)
        except Exception as exc:
            logger.error('Failed to assign barcode %s to Part pk=%s: %s', barcode_data, part.pk, exc)

        if self.get_setting('SET_IMAGE') and product.image_url:
            self._set_part_image(part, product.image_url)

        logger.info(
            'Auto-created Part pk=%s name="%s" from barcode %s (source: %s)',
            part.pk, name, barcode_data, product.source,
        )
        return part

    def _create_placeholder_part(self, barcode_data: str):
        """Create a placeholder Part when the barcode isn't in any external DB.

        The Part is named with the barcode so the user can identify and
        rename it later.
        """
        from part.models import Part, PartCategory

        barcode_len = len(barcode_data)
        if barcode_len == 12:
            barcode_type = 'UPC-A'
        elif barcode_len == 13:
            barcode_type = 'EAN-13'
        else:
            barcode_type = 'EAN-8'

        name = f'Unknown Product ({barcode_data})'
        description = (
            f'{barcode_type} barcode {barcode_data} — not found in product databases. '
            f'Edit this Part to add the correct name and details.'
        )

        category = None
        cat_id = self._resolve_setting_int('DEFAULT_CATEGORY')
        if cat_id and cat_id > 0:
            category = PartCategory.objects.filter(pk=cat_id).first()

        try:
            part = Part.objects.create(
                name=name,
                description=description,
                category=category,
                component=False,
                purchaseable=True,
                active=True,
            )
        except Exception as exc:
            logger.error('Failed to create placeholder Part for barcode %s: %s', barcode_data, exc)
            return None

        try:
            part.assign_barcode(barcode_data=barcode_data, raise_error=False)
        except Exception as exc:
            logger.error('Failed to assign barcode %s to Part pk=%s: %s', barcode_data, part.pk, exc)

        logger.info(
            'Created placeholder Part pk=%s for unknown barcode %s',
            part.pk, barcode_data,
        )
        return part

    def _create_stock_item(self, part, location_id: int, quantity: int = 1):
        """Create a StockItem for the given Part at the specified location."""
        from stock.models import StockItem, StockLocation

        location = StockLocation.objects.filter(pk=location_id).first()
        if location is None:
            logger.warning('Stock location pk=%s not found; skipping stock creation.', location_id)
            return None

        try:
            item = StockItem.objects.create(
                part=part,
                location=location,
                quantity=quantity,
            )
            logger.info(
                'Created StockItem pk=%s (qty=%s) for Part pk=%s at location "%s" (pk=%s)',
                item.pk, quantity, part.pk, location.name, location.pk,
            )
            return item
        except Exception as exc:
            logger.error(
                'Failed to create StockItem for Part pk=%s at location pk=%s: %s',
                part.pk, location_id, exc,
            )
            return None

    def _set_part_image(self, part, image_url: str):
        """Download and set a product image on the Part."""
        import io

        import requests
        from django.core.files.base import ContentFile

        try:
            resp = requests.get(image_url, timeout=15, stream=True)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning('Failed to download image from %s: %s', image_url, exc)
            return

        content_type = resp.headers.get('Content-Type', '')
        if 'image' not in content_type:
            logger.warning('URL %s returned non-image content-type: %s', image_url, content_type)
            return

        ext_map = {
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
        }
        ext = ext_map.get(content_type.split(';')[0].strip(), '.jpg')

        max_size = 5 * 1024 * 1024
        data = io.BytesIO()
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > max_size:
                logger.warning('Image from %s exceeds 5MB limit, skipping', image_url)
                return
            data.write(chunk)

        filename = f'barcode_{part.pk}{ext}'
        try:
            part.image.save(filename, ContentFile(data.getvalue()), save=True)
            logger.info('Set image on Part pk=%s from %s', part.pk, image_url)
        except Exception as exc:
            logger.error('Failed to save image for Part pk=%s: %s', part.pk, exc)

    def _resolve_setting_int(self, key: str) -> int | None:
        """Get a plugin setting as an integer, returning None on failure."""
        val = self.get_setting(key)
        if not val:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
