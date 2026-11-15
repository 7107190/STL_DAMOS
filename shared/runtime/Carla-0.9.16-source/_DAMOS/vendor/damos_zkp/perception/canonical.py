"""One canonical encoder shared by sender and receiver. No floats in commitments."""
import json
import math
from decimal import Decimal, ROUND_HALF_UP

def assert_integer_json(value, depth=0):
    if depth > 20:
        raise ValueError('JSON_TOO_DEEP')
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is list:
        for item in value:
            assert_integer_json(item, depth + 1)
        return
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            assert_integer_json(item, depth + 1)
        return
    raise ValueError('NON_INTEGER_CANONICAL_JSON:' + type(value).__name__)

def canonical_json_bytes(value):
    assert_integer_json(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')

def quantize(value, scale=1000):
    """Round nearest, ties AWAY from zero (also for negative coordinates).
    Decimal(str(float)) specifies how an incoming binary float is interpreted.
    Reduction (AABB/min distance) happens before quantization.
    """
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError('NON_FINITE_MEASUREMENT')
    return int((Decimal(str(value)) * Decimal(scale)).to_integral_value(rounding=ROUND_HALF_UP))
