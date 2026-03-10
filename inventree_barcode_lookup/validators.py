"""UPC/EAN barcode format detection and check digit validation."""

import re


def _gtin_check_digit(digits: str) -> int:
    """Compute the GTIN check digit (used by UPC-A, EAN-8, EAN-13).

    Applies the standard GS1 algorithm:
    - Odd positions (from right, excluding check digit) weighted x3
    - Even positions weighted x1
    - Check digit = (10 - sum % 10) % 10
    """
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        total += n * 3 if i % 2 == 0 else n
    return (10 - total % 10) % 10


def is_upc_a(barcode: str) -> bool:
    """12-digit UPC-A with valid check digit."""
    if not re.fullmatch(r'\d{12}', barcode):
        return False
    return _gtin_check_digit(barcode[:11]) == int(barcode[11])


def is_ean_13(barcode: str) -> bool:
    """13-digit EAN-13 with valid check digit."""
    if not re.fullmatch(r'\d{13}', barcode):
        return False
    return _gtin_check_digit(barcode[:12]) == int(barcode[12])


def is_ean_8(barcode: str) -> bool:
    """8-digit EAN-8 with valid check digit."""
    if not re.fullmatch(r'\d{8}', barcode):
        return False
    return _gtin_check_digit(barcode[:7]) == int(barcode[7])


def is_retail_barcode(barcode: str) -> bool:
    """Return True if the barcode looks like a retail UPC-A, EAN-13, or EAN-8."""
    if not isinstance(barcode, str):
        return False
    barcode = barcode.strip()
    return is_upc_a(barcode) or is_ean_13(barcode) or is_ean_8(barcode)


def normalize_barcode(barcode: str) -> str:
    """Normalize a barcode to EAN-13 format for consistent lookups.

    UPC-A (12 digits) is zero-padded to 13 digits.
    EAN-8 is left as-is (some APIs handle it natively).
    """
    barcode = barcode.strip()
    if len(barcode) == 12:
        return '0' + barcode
    return barcode
