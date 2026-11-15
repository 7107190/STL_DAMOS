"""Anchor broadcast payload schema.

The OBJECT schema is imported unchanged from perception.schema, so an object
leaf produced by a modality's own Semantic LiDAR and an object leaf produced by
the anchor's fusion are byte-identical for identical values. Only the payload
envelope differs: an anchor result is expressed in a shared frame and carries
the set of contributing sources instead of a single sensor_id.
"""
from perception.canonical import canonical_json_bytes
from perception.schema import normalize_objects, require_int, require_text

MAX_SOURCES = 16
ALLOWED_FRAMES = ('world', 'anchor')

PAYLOAD_FIELDS = ('schema_version', 'anchor_id', 'fusion_id', 'anchor_timestamp_ms',
                  'coordinate_frame', 'source_ids', 'objects')


def normalize_sources(sources):
    if type(sources) is not list or not sources or len(sources) > MAX_SOURCES:
        raise ValueError('INVALID_SOURCE_ARRAY')
    seen = set()
    for name in sources:
        require_text(name, 'source_id', 64)
        if name in seen:
            raise ValueError('DUPLICATE_SOURCE_ID')
        seen.add(name)
    return sorted(sources)


def normalize_broadcast_payload(payload):
    if type(payload) is not dict or set(payload) != set(PAYLOAD_FIELDS):
        raise ValueError('INVALID_BROADCAST_FIELDS')
    for name in ('schema_version', 'fusion_id', 'anchor_timestamp_ms'):
        require_int(payload[name], name)
    if payload['schema_version'] != 1:
        raise ValueError('UNSUPPORTED_BROADCAST_SCHEMA')
    if payload['coordinate_frame'] not in ALLOWED_FRAMES:
        raise ValueError('UNSUPPORTED_COORDINATE_FRAME')
    require_text(payload['anchor_id'], 'anchor_id')
    out = dict(payload)
    out['source_ids'] = normalize_sources(payload['source_ids'])
    out['objects'] = normalize_objects(payload['objects'])
    return out


def canonical_broadcast_bytes(payload):
    return canonical_json_bytes(normalize_broadcast_payload(payload))
