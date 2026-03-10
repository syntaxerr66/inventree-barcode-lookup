"""InvenTree plugin that resolves retail UPC/EAN barcodes via external product databases."""

import logging

from plugin import InvenTreePlugin
from plugin.mixins import BarcodeMixin, SettingsMixin

from .lookup import lookup_product
from .validators import is_retail_barcode

logger = logging.getLogger('inventree')


class RetailBarcodePlugin(BarcodeMixin, SettingsMixin, InvenTreePlugin):
    """Resolve retail UPC/EAN barcodes against external product databases.

    When a barcode is scanned:
    1. Check if it's a valid UPC-A, EAN-13, or EAN-8 format (with check digit validation).
    2. Check if it's already assigned to a Part in the database.
    3. Look up the barcode in external databases (UPCitemdb, Open Food Facts).
    4. Optionally auto-create a Part from the lookup result.
    """

    NAME = 'RetailBarcodePlugin'
    SLUG = 'retail-barcode'
    TITLE = 'Retail Barcode Lookup'
    DESCRIPTION = 'Resolve retail UPC/EAN barcodes against product databases and optionally auto-create parts'
    VERSION = '0.1.0'
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
    }

    def scan(self, barcode_data):
        """Handle a barcode scan event.

        Called by InvenTree's barcode API when a barcode is scanned.
        Returns a dict with the matched Part data, or None to pass
        to the next barcode plugin.
        """
        # Only handle string barcodes (not JSON/dict from InvenTree's own format)
        if not isinstance(barcode_data, str):
            return None

        barcode_data = barcode_data.strip()

        # Step 1: Is this a retail barcode format?
        if not is_retail_barcode(barcode_data):
            return None

        logger.info('Retail barcode detected: %s', barcode_data)

        # Step 2: Check if already assigned to a Part
        existing = self._find_existing_part(barcode_data)
        if existing is not None:
            logger.info('Barcode %s matched existing Part pk=%s', barcode_data, existing.pk)
            return {
                'part': existing.format_matched_response(),
            }

        # Step 3: Look up in external databases
        product = lookup_product(barcode_data)

        if product is None:
            logger.info('Barcode %s not found in any external database', barcode_data)
            return None

        logger.info('Barcode %s resolved to: %s', barcode_data, product)

        # Step 4: Auto-create or return "not found"
        if not self.get_setting('AUTO_CREATE_PARTS'):
            logger.info(
                'Auto-create disabled. Found product "%s" for barcode %s but not creating Part.',
                product.full_name(), barcode_data,
            )
            return None

        part = self._create_part(barcode_data, product)
        if part is None:
            return None

        return {
            'part': part.format_matched_response(),
        }

    def _find_existing_part(self, barcode_data: str):
        """Check if any Part already has this barcode assigned."""
        from InvenTree.helpers import hash_barcode
        from part.models import Part

        barcode_hash = hash_barcode(barcode_data)
        return Part.lookup_barcode(barcode_hash)

    def _create_part(self, barcode_data: str, product):
        """Create a new Part from external product lookup data."""
        from part.models import Part, PartCategory

        # Determine the part name
        name_format = self.get_setting('NAME_FORMAT')
        if name_format == 'brand_name':
            name = product.full_name()
        else:
            name = product.name

        # Truncate to model field max_length (avoid DB errors)
        name = name[:100]

        # Build description from available data
        desc_parts = []
        if product.description:
            desc_parts.append(product.description)
        if product.category:
            desc_parts.append(f'Category: {product.category}')
        desc_parts.append(f'Barcode: {barcode_data}')
        if product.source:
            desc_parts.append(f'Source: {product.source}')
        description = ' | '.join(desc_parts)[:250]

        # Resolve category
        category = None
        cat_id = self.get_setting('DEFAULT_CATEGORY')
        if cat_id:
            try:
                cat_id = int(cat_id)
                if cat_id > 0:
                    category = PartCategory.objects.filter(pk=cat_id).first()
            except (ValueError, TypeError):
                pass

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

        # Assign barcode
        try:
            part.assign_barcode(barcode_data=barcode_data, raise_error=False)
        except Exception as exc:
            logger.error('Failed to assign barcode %s to Part pk=%s: %s', barcode_data, part.pk, exc)
            # Part was created but barcode wasn't assigned — still usable

        # Set product image
        if self.get_setting('SET_IMAGE') and product.image_url:
            self._set_part_image(part, product.image_url)

        logger.info(
            'Auto-created Part pk=%s name="%s" from barcode %s (source: %s)',
            part.pk, name, barcode_data, product.source,
        )
        return part

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

        # Determine file extension from content type
        ext_map = {
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
        }
        ext = ext_map.get(content_type.split(';')[0].strip(), '.jpg')

        # Read image data (limit to 5MB)
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
