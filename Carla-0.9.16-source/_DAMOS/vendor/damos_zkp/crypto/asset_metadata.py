"""Read-only binary asset checks; NOT a replacement for Groth16 verification.

ZKEY layout reference: iden3/snarkjs v0.7.6 src/zkey_utils.js.
All parsing uses stdlib so legacy files can be diagnosed before npm installation.
"""
import hashlib
import json
import struct
from pathlib import Path

AUTH_NAMES = {
    'commitment_wasm': 'vehicle_commitment_js/vehicle_commitment.wasm',
    'auth_wasm': 'vehicle_perception_auth_js/vehicle_perception_auth.wasm',
    'auth_zkey': 'vehicle_perception_auth_final.zkey',
    'verification_key': 'vehicle_perception_auth_verification_key.json',
}
LEGACY_NAMES = {
    'commitment_wasm': 'vehicle_commitment_js/vehicle_commitment.wasm',
    'auth_wasm': 'vehicle_auth_js/vehicle_auth.wasm',
    'auth_zkey': 'vehicle_auth_final.zkey',
    'verification_key': 'vehicle_auth_verification_key.json',
}
FIELD = 21888242871839275222246405745257275088548364400416034343698204186575808495617
BASE_FIELD = 21888242871839275222246405745257275088696311157297823662689037894645226208583


def file_hash(path, algorithm='sha256'):
    digest = hashlib.new(algorithm)
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def binary_sections(path, expected_magic):
    """Return section offsets, rejecting truncated/ambiguous section tables."""
    path = Path(path)
    size = path.stat().st_size
    sections = {}
    with path.open('rb') as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != expected_magic:
            raise ValueError('INVALID_BINARY_MAGIC:' + path.name)
        version, count = struct.unpack('<II', header[4:])
        if version not in (1, 2) or count > 100:
            raise ValueError('UNSUPPORTED_BINARY_HEADER:' + path.name)
        for _ in range(count):
            raw = stream.read(12)
            if len(raw) != 12:
                raise ValueError('TRUNCATED_SECTION_HEADER:' + path.name)
            kind, length = struct.unpack('<IQ', raw)
            offset = stream.tell()
            if kind in sections or offset + length > size:
                raise ValueError('INVALID_BINARY_SECTION:' + path.name)
            sections[kind] = (offset, length)
            stream.seek(length, 1)
        if stream.tell() != size:
            raise ValueError('TRAILING_BINARY_DATA:' + path.name)
    return version, sections


def _read_section(path, sections, kind, limit=4096):
    if kind not in sections:
        raise ValueError('MISSING_BINARY_SECTION:' + str(kind))
    offset, length = sections[kind]
    if length > limit:
        raise ValueError('OVERSIZED_BINARY_HEADER')
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        return stream.read(length)


def zkey_header(path):
    version, sections = binary_sections(path, b'zkey')
    proto = _read_section(path, sections, 1)
    if proto != struct.pack('<I', 1):
        raise ValueError('NOT_GROTH16_ZKEY')
    raw = _read_section(path, sections, 2)
    pos = 0
    def read_u32():
        nonlocal pos
        if pos + 4 > len(raw):
            raise ValueError('TRUNCATED_GROTH16_HEADER')
        value = struct.unpack_from('<I', raw, pos)[0]
        pos += 4
        return value
    def read_field():
        nonlocal pos
        width = read_u32()
        if width != 32 or pos + width > len(raw):
            raise ValueError('EXPECTED_BN254_FIELD_ENCODING')
        value = int.from_bytes(raw[pos:pos + width], 'little')
        pos += width
        return value
    q, r = read_field(), read_field()
    nvars, npublic, domain = read_u32(), read_u32(), read_u32()
    if q != BASE_FIELD or r != FIELD:
        raise ValueError('EXPECTED_BN254_ZKEY')
    if not 1 <= nvars or not 0 <= npublic < nvars or domain < 1 or domain & (domain - 1):
        raise ValueError('INVALID_GROTH16_DIMENSIONS')
    # Three G1 and three G2 points follow the scalar header.
    if len(raw) - pos != 3 * 64 + 3 * 128:
        raise ValueError('INVALID_GROTH16_POINT_HEADER_SIZE')
    if 3 not in sections or sections[3][1] != (npublic + 1) * 64:
        raise ValueError('INVALID_ZKEY_IC_SECTION_LENGTH')
    return dict(protocol='groth16', curve='bn128', version=version,
                nPublic=npublic, nVars=nvars, domainSize=domain)


def r1cs_header(path):
    version, sections = binary_sections(path, b'r1cs')
    raw = _read_section(path, sections, 1)
    if len(raw) < 4:
        raise ValueError('INVALID_R1CS_HEADER')
    width = struct.unpack_from('<I', raw)[0]
    if width != 32 or len(raw) != 4 + width + 4 * 4 + 8 + 4:
        raise ValueError('INVALID_BN254_R1CS_HEADER_SIZE')
    prime = int.from_bytes(raw[4:4 + width], 'little')
    if prime != FIELD:
        raise ValueError('EXPECTED_BN254_R1CS')
    wires, outputs, public, private, labels, constraints = struct.unpack_from('<IIIIQI', raw, 4 + width)
    return dict(version=version, nWires=wires, nOutputs=outputs, nPublicInputs=public,
                nPrivateInputs=private, nLabels=labels, nConstraints=constraints)


def verify_symbol_order(path):
    found = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        parts = line.split(',', 3)
        if len(parts) == 4 and parts[3] in (
                'main.registered_commitment', 'main.context_hash', 'main.payload_hash'):
            found[parts[3]] = int(parts[1])
    expected = {'main.registered_commitment': 1, 'main.context_hash': 2, 'main.payload_hash': 3}
    if found != expected:
        raise ValueError('CIRCUIT_PUBLIC_SIGNAL_ORDER_MISMATCH:' + repr(found))
    return found


def paths_for(base):
    base = Path(base).expanduser().resolve()
    return {key: base / name for key, name in AUTH_NAMES.items()}


def resolve_asset_directory(base):
    """Accept an explicit v2 directory or a legacy parent containing perception_v2.

    Never relabel a legacy one-public-input key as a three-public-input key.
    """
    base = Path(base).expanduser().resolve()
    if (base / AUTH_NAMES['verification_key']).is_file():
        return base
    child = base / 'perception_v2'
    if (child / AUTH_NAMES['verification_key']).is_file():
        return child
    if (base / LEGACY_NAMES['verification_key']).is_file():
        raise ValueError('LEGACY_NPUBLIC_1_REBUILD_REQUIRED: run python -m '
                         'scripts.connect_groth16 --legacy-dir "{}"'.format(base))
    return base


def inspect_legacy(base):
    base = Path(base).expanduser().resolve()
    files, errors = {}, []
    result = dict(directory=str(base), files=files, errors=errors,
                  status='NOT_READY', note='Metadata inspection, not pairing/proof verification.')
    for name, rel in LEGACY_NAMES.items():
        path = base / rel
        files[name] = dict(path=str(path), exists=path.is_file())
        if not path.is_file():
            errors.append('MISSING_LEGACY_ASSET:' + name)
            continue
        files[name].update(size_bytes=path.stat().st_size, sha256=file_hash(path))
        if name.endswith('wasm'):
            with path.open('rb') as stream:
                if stream.read(8) != b'\x00asm\x01\x00\x00\x00':
                    errors.append('INVALID_WASM_HEADER:' + name)
    if not errors:
        try:
            vk = json.loads((base / LEGACY_NAMES['verification_key']).read_text(encoding='utf-8-sig'))
            zk = zkey_header(base / LEGACY_NAMES['auth_zkey'])
            result.update(nPublic=vk.get('nPublic'), zkey_header=zk,
                          public_count_agrees=vk.get('nPublic') == zk['nPublic'])
            if not result['public_count_agrees']:
                errors.append('LEGACY_VKEY_ZKEY_PUBLIC_COUNT_MISMATCH')
            elif vk.get('protocol') != 'groth16' or vk.get('curve') not in ('bn128', 'bn254'):
                errors.append('UNSUPPORTED_LEGACY_VERIFICATION_KEY')
            elif vk.get('nPublic') != 1:
                errors.append('LEGACY_LAYOUT_EXPECTS_ONE_PUBLIC_INPUT')
            else:
                result['status'] = 'LEGACY_ONE_PUBLIC_INPUT_REBUILD_REQUIRED'
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
    return result
