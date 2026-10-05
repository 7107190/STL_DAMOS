"""Strict integer-unit perception schema. Unknown fields and duplicate IDs fail closed."""
from app.config import MAX_OBJECTS
from perception.canonical import canonical_json_bytes

OBJECT_FIELDS = ('object_id', 'semantic_tag', 'center_x_mm', 'center_y_mm', 'center_z_mm',
                 'length_mm', 'width_mm', 'height_mm', 'min_distance_mm', 'point_count')
PAYLOAD_FIELDS = ('schema_version', 'frame_id', 'sensor_timestamp_ms', 'sensor_id', 'coordinate_frame', 'objects')

def require_int(value, name, low=0, high=2**63-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('INVALID_INTEGER:' + name)

def require_text(value, name, max_len=128):
    if type(value) is not str or not value or len(value) > max_len or any(ord(c) < 32 for c in value):
        raise ValueError('INVALID_TEXT:' + name)

def normalize_objects(objects):
    if type(objects) is not list or len(objects) > MAX_OBJECTS:
        raise ValueError('INVALID_OBJECT_ARRAY')
    result, ids = [], set()
    for obj in objects:
        if type(obj) is not dict or set(obj) != set(OBJECT_FIELDS):
            raise ValueError('INVALID_OBJECT_FIELDS')
        for key in OBJECT_FIELDS:
            low = -2000000000 if key.startswith('center_') else 0
            high = 4294967295 if key == 'object_id' else 2000000000
            require_int(obj[key], key, low, high)
        if obj['object_id'] == 0 or obj['point_count'] < 1 or obj['semantic_tag'] > 255:
            raise ValueError('INVALID_OBJECT_VALUE')
        if obj['object_id'] in ids:
            raise ValueError('DUPLICATE_OBJECT_ID')
        ids.add(obj['object_id'])
        result.append(dict(obj))
    return sorted(result, key=lambda item: item['object_id'])

def normalize_payload(payload):
    if type(payload) is not dict or set(payload) != set(PAYLOAD_FIELDS):
        raise ValueError('INVALID_PERCEPTION_FIELDS')
    for name in ('schema_version', 'frame_id', 'sensor_timestamp_ms'):
        require_int(payload[name], name)
    if payload['schema_version'] != 1 or payload['coordinate_frame'] != 'sensor':
        raise ValueError('UNSUPPORTED_SCHEMA_OR_COORDINATES')
    require_text(payload['sensor_id'], 'sensor_id')
    out = dict(payload)
    out['objects'] = normalize_objects(payload['objects'])
    return out

def canonical_payload_bytes(payload):
    return canonical_json_bytes(normalize_payload(payload))
