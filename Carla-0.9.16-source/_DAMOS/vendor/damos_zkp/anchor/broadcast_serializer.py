"""Strict JSON wire encoding for anchor broadcasts. No Base64, no fallback to a
live Python object on decode failure. Duplicate JSON keys, NaN/Infinity and
oversized wires are rejected before any expensive check runs."""
import json
import time
from dataclasses import asdict, fields

from anchor.broadcast_packet import AnchorBroadcastPacket
from app.config import MAX_PACKET_BYTES


class BroadcastDecodeError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BroadcastDecodeError('DUPLICATE_JSON_KEY:' + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise BroadcastDecodeError('NON_FINITE_JSON:' + value)


class BroadcastSerializer:
    @staticmethod
    def to_dict(pkt):
        if type(pkt) is not AnchorBroadcastPacket:
            raise BroadcastDecodeError('WRONG_PACKET_CLASS')
        return asdict(pkt)

    @staticmethod
    def from_dict(value):
        required = set(f.name for f in fields(AnchorBroadcastPacket))
        if type(value) is not dict or set(value) != required:
            raise BroadcastDecodeError('PACKET_KEYS_MISMATCH')
        # No coercion: "12" must not silently become integer 12.
        return AnchorBroadcastPacket(**value)

    @staticmethod
    def to_bytes(pkt):
        try:
            wire = json.dumps(BroadcastSerializer.to_dict(pkt), sort_keys=True,
                              separators=(',', ':'), ensure_ascii=True,
                              allow_nan=False).encode('utf-8')
            if len(wire) > MAX_PACKET_BYTES:
                raise BroadcastDecodeError('PACKET_TOO_LARGE')
            return wire
        except (ValueError, TypeError, RecursionError) as exc:
            raise BroadcastDecodeError(str(exc))

    @staticmethod
    def from_bytes(wire):
        try:
            if type(wire) is not bytes or len(wire) > MAX_PACKET_BYTES:
                raise BroadcastDecodeError('INVALID_WIRE_OR_SIZE')
            value = json.loads(wire.decode('utf-8'), object_pairs_hook=_unique_pairs,
                               parse_constant=_reject_constant)
            return BroadcastSerializer.from_dict(value)
        except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
            raise BroadcastDecodeError(str(exc))

    @staticmethod
    def to_bytes_measured(pkt):
        start = time.perf_counter()
        wire = BroadcastSerializer.to_bytes(pkt)
        pkt.auth_metrics['serialize_ms'] = (time.perf_counter() - start) * 1000
        pkt.auth_metrics['packet_size_bytes'] = len(wire)
        return wire

    @staticmethod
    def from_bytes_measured(wire):
        start = time.perf_counter()
        pkt = BroadcastSerializer.from_bytes(wire)
        if type(pkt.auth_metrics) is not dict:
            raise BroadcastDecodeError('INVALID_METRICS')
        pkt.auth_metrics['deserialize_ms'] = (time.perf_counter() - start) * 1000
        pkt.auth_metrics['packet_size_bytes'] = len(wire)
        return pkt
