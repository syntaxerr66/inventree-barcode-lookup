"""Custom API endpoints for interactive location selection and scan-to-stock.

These endpoints complement the standard BarcodeMixin.scan() flow by providing:
- Location tree browsing (drill into sublocations)
- Explicit scan-to-stock with location + quantity
- Per-user "last used location" tracking
"""

import logging

from django.core.cache import cache
from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from InvenTree.permissions import RolePermission

logger = logging.getLogger('inventree')

# Cache timeout for last-used location: 90 days
LAST_LOCATION_CACHE_TIMEOUT = 90 * 24 * 60 * 60


def _get_plugin():
    """Get the active RetailBarcodePlugin instance from InvenTree's registry."""
    from plugin.registry import registry
    return registry.get_plugin('retail-barcode')


def _cache_key(user):
    """Per-user cache key for last-used stock location."""
    return f'retail_barcode_last_loc_{user.pk}'


def _serialize_location(loc):
    """Serialize a StockLocation to a dict."""
    return {
        'id': loc.pk,
        'name': loc.name,
        'description': loc.description or '',
        'pathstring': loc.pathstring,
        'parent_id': loc.parent_id,
        'has_children': loc.children.exists(),
        'child_count': loc.children.count(),
        'items_count': loc.stock_items.count(),
        'location_type': loc.location_type.name if loc.location_type else None,
    }


class LocationTreeView(APIView):
    """Browse stock locations as a tree.

    GET /plugin/retail-barcode/api/locations/
    Query params:
        parent  - Parent location ID. Omit or 0 for top-level locations.

    Returns:
        {
            "locations": [...],          # Child locations at this level
            "current": {...} | null,     # Parent location details (null for root)
            "breadcrumbs": [...]         # Path from root to current location
        }
    """

    permission_classes = [IsAuthenticated, RolePermission]
    role_required = 'stock_location'

    def get(self, request):
        from stock.models import StockLocation

        parent_id = request.query_params.get('parent')

        # Parse parent_id
        try:
            parent_id = int(parent_id) if parent_id else 0
        except (ValueError, TypeError):
            parent_id = 0

        # Fetch children
        if parent_id > 0:
            locations = StockLocation.objects.filter(parent_id=parent_id)
        else:
            locations = StockLocation.objects.filter(parent=None)

        data = [_serialize_location(loc) for loc in locations.order_by('name')]

        # Current location info + breadcrumbs
        current = None
        breadcrumbs = []

        if parent_id > 0:
            try:
                parent = StockLocation.objects.get(pk=parent_id)
                current = _serialize_location(parent)
                # Build breadcrumbs by walking up the tree
                node = parent
                while node is not None:
                    breadcrumbs.insert(0, {
                        'id': node.pk,
                        'name': node.name,
                    })
                    node = node.parent
            except StockLocation.DoesNotExist:
                pass

        return JsonResponse({
            'locations': data,
            'current': current,
            'breadcrumbs': breadcrumbs,
        })


class ScanToStockView(APIView):
    """Scan a retail barcode and create stock at a specific location.

    POST /plugin/retail-barcode/api/scan-to-stock/
    Body (JSON):
        barcode      - UPC-A, EAN-13, or EAN-8 barcode string (required)
        location_id  - Stock location ID (optional, defaults to last-used or plugin default)
        quantity     - Quantity to add (optional, defaults to plugin DEFAULT_QUANTITY)

    This endpoint always creates a Part if one doesn't exist (regardless of the
    AUTO_CREATE_PARTS setting), because the user is explicitly requesting stock creation.
    It also saves the selected location as the user's last-used location.
    """

    permission_classes = [IsAuthenticated, RolePermission]
    role_required = 'stock.add'

    def post(self, request):
        from stock.models import StockLocation

        from .validators import is_retail_barcode

        plugin = _get_plugin()
        if plugin is None:
            return JsonResponse({'error': 'Retail Barcode plugin is not active.'}, status=503)

        barcode = (request.data.get('barcode') or '').strip()
        if not barcode:
            return JsonResponse({'error': 'barcode is required.'}, status=400)

        if not is_retail_barcode(barcode):
            return JsonResponse({
                'error': 'Not a valid retail barcode (UPC-A, EAN-13, or EAN-8).',
            }, status=400)

        # Resolve location: explicit > last-used > plugin default
        location_id = request.data.get('location_id')
        if location_id is not None:
            try:
                location_id = int(location_id)
            except (ValueError, TypeError):
                return JsonResponse({'error': 'location_id must be an integer.'}, status=400)
        else:
            # Fall back to last-used, then plugin default
            location_id = cache.get(_cache_key(request.user))
            if location_id is None:
                location_id = plugin._resolve_setting_int('DEFAULT_LOCATION')

        # Validate location exists
        location = None
        if location_id and location_id > 0:
            location = StockLocation.objects.filter(pk=location_id).first()
            if location is None:
                return JsonResponse({
                    'error': f'Stock location {location_id} not found.',
                }, status=404)

        # Resolve quantity
        quantity = request.data.get('quantity')
        if quantity is not None:
            try:
                quantity = int(quantity)
                if quantity < 1:
                    return JsonResponse({'error': 'quantity must be at least 1.'}, status=400)
            except (ValueError, TypeError):
                return JsonResponse({'error': 'quantity must be an integer.'}, status=400)
        else:
            quantity = plugin._resolve_setting_int('DEFAULT_QUANTITY') or 1

        # Find or create Part (always creates — user is explicitly adding stock)
        part, created = plugin.find_or_create_part(barcode)
        if part is None:
            return JsonResponse({
                'error': (
                    'Barcode not found in any product database. '
                    'The product may not exist in UPCitemdb or Open Food Facts.'
                ),
            }, status=404)

        # Create StockItem
        stock_item = None
        if location is not None:
            stock_item = plugin._create_stock_item(part, location.pk, quantity)

            # Save last-used location for this user
            cache.set(_cache_key(request.user), location.pk, timeout=LAST_LOCATION_CACHE_TIMEOUT)

        response = {
            'success': True,
            'part': {
                'pk': part.pk,
                'name': part.name,
                'description': part.description,
                'created': created,
            },
        }

        if stock_item is not None:
            response['stock_item'] = {
                'pk': stock_item.pk,
                'quantity': stock_item.quantity,
                'location': {
                    'pk': location.pk,
                    'name': location.name,
                    'pathstring': location.pathstring,
                },
            }
        elif location is None:
            response['warning'] = (
                'No location specified. Part was found/created but no stock was added. '
                'Provide location_id or set a Default Stock Location in plugin settings.'
            )

        return JsonResponse(response)


class LastLocationView(APIView):
    """Get or set the user's last-used stock location.

    GET /plugin/retail-barcode/api/last-location/
        Returns the last location this user scanned stock into.

    PUT /plugin/retail-barcode/api/last-location/
        Body: {"location_id": <int>}
        Explicitly set the user's default location.

    DELETE /plugin/retail-barcode/api/last-location/
        Clear the user's last-used location (revert to plugin default).
    """

    permission_classes = [IsAuthenticated, RolePermission]
    role_required = 'stock_location'

    def get(self, request):
        from stock.models import StockLocation

        plugin = _get_plugin()

        # Try user's cached last location
        location_id = cache.get(_cache_key(request.user))
        source = 'last_used'

        # Fall back to plugin default
        if location_id is None and plugin is not None:
            location_id = plugin._resolve_setting_int('DEFAULT_LOCATION')
            source = 'plugin_default'

        if not location_id or location_id <= 0:
            return JsonResponse({
                'location': None,
                'source': None,
                'message': 'No default location set. Select a location when scanning.',
            })

        location = StockLocation.objects.filter(pk=location_id).first()
        if location is None:
            return JsonResponse({
                'location': None,
                'source': source,
                'message': f'Previously saved location (pk={location_id}) no longer exists.',
            })

        return JsonResponse({
            'location': _serialize_location(location),
            'source': source,
        })

    def put(self, request):
        from stock.models import StockLocation

        location_id = request.data.get('location_id')
        if location_id is None:
            return JsonResponse({'error': 'location_id is required.'}, status=400)

        try:
            location_id = int(location_id)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'location_id must be an integer.'}, status=400)

        if location_id <= 0:
            # Clear last location
            cache.delete(_cache_key(request.user))
            return JsonResponse({'success': True, 'message': 'Last location cleared.'})

        location = StockLocation.objects.filter(pk=location_id).first()
        if location is None:
            return JsonResponse({'error': f'Stock location {location_id} not found.'}, status=404)

        cache.set(_cache_key(request.user), location_id, timeout=LAST_LOCATION_CACHE_TIMEOUT)
        return JsonResponse({
            'success': True,
            'location': _serialize_location(location),
        })

    def delete(self, request):
        cache.delete(_cache_key(request.user))
        return JsonResponse({'success': True, 'message': 'Last location cleared.'})
