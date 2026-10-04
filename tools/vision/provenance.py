"""Deterministic provider provenance; never synthesizes a clock timestamp."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import math
import re

from .contract import _CREATED_AT_PATTERN, _require, _text


def validate_created_at(value):
    if value is None:
        return None
    _text(value, 'timestamp')
    _require(re.fullmatch(_CREATED_AT_PATTERN, value) is not None, 'invalid provider timestamp')
    try:
        stamp = datetime.fromisoformat(value)
        _require(stamp.utcoffset() is not None, 'invalid provider timestamp')
    except ValueError:
        _require(False, 'invalid provider timestamp')
    return value


def unix_created_at(value):
    """Convert finite nonnegative Unix seconds to UTC ISO, at microsecond precision."""
    _require(type(value) in (int, float), 'invalid provider Unix timestamp')
    _require(type(value) is int or math.isfinite(value), 'invalid provider Unix timestamp')
    try:
        seconds = Decimal(str(value))
        _require(0 <= seconds < Decimal('253402300800'), 'invalid provider Unix timestamp')
        with localcontext() as context:
            context.prec = 40
            micros = int((seconds * 1_000_000).to_integral_value(rounding=ROUND_HALF_EVEN))
        stamp = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=micros)
    except (ValueError, OverflowError, InvalidOperation):
        _require(False, 'invalid provider Unix timestamp')
    return validate_created_at(stamp.isoformat().replace('+00:00', 'Z'))
