import hashlib
import json
from pathlib import Path
from crypto.asset_metadata import zkey_header

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'assets' / 'perception_v2'
EXPECTED = {
    'vehicle_commitment_js/vehicle_commitment.wasm': '9c44de901de070b5e67ca6be1fee9a5bb25fbb96c25161baadebc832d9a316f6',
    'vehicle_perception_auth_js/vehicle_perception_auth.wasm': 'da74857a67f60765968bac8f2c244e4a2074731de0d089be463291fe5577fca4',
    'vehicle_perception_auth_final.zkey': 'b47f4ce61378f9b4b7d8a3935c2bbb9121a99118e5f1061d5cff2c7fa6778e7f',
    'vehicle_perception_auth_verification_key.json': '183df16a0abf08647511432ed273a1084574ef59b81551ca5b7ee54e98e78765',
}

def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

for rel, expected in EXPECTED.items():
    path = BASE / rel
    if not path.is_file():
        raise SystemExit('MISSING: ' + str(path))
    actual = sha256(path)
    if actual != expected:
        raise SystemExit('HASH_MISMATCH: {}\nexpected={}\nactual={}'.format(rel, expected, actual))

vk = json.loads((BASE / 'vehicle_perception_auth_verification_key.json').read_text(encoding='utf-8-sig'))
if vk.get('protocol') != 'groth16' or vk.get('nPublic') != 3:
    raise SystemExit('INVALID_VERIFICATION_KEY')
zk = zkey_header(BASE / 'vehicle_perception_auth_final.zkey')
if zk.get('nPublic') != 3:
    raise SystemExit('INVALID_ZKEY_PUBLIC_INPUT_COUNT')
print('PASS: Groth16 runtime assets match the handoff manifest; nPublic=3.')
