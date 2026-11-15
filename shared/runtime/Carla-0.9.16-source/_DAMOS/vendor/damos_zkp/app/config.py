"""Minimal configuration for the DAMOS security handoff package.

No CARLA or user-specific paths are required. By default, Groth16 assets are
loaded from ./assets/perception_v2 and snarkjs from ./node_modules.
"""
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _path_env(name, default):
    value = os.environ.get(name)
    p = Path(value).expanduser() if value else Path(default)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

def _flag(name, default=False):
    return os.environ.get(name, '1' if default else '0').lower() in ('1', 'true', 'yes')

MAX_OBJECTS = int(os.environ.get('V2V_MAX_OBJECTS', '2048'))
MAX_PACKET_BYTES = int(os.environ.get('V2V_MAX_PACKET_BYTES', '2097152'))
SNARK_FIELD_MODULUS = 21888242871839275222246405745257275088548364400416034343698204186575808495617
ZKP_CONTEXT_DOMAIN = b'DAMOS/V2V/CONTEXT/v1\x00'
ZKP_BASE_DIR = _path_env('V2V_ZKP_BASE_DIR', PROJECT_ROOT / 'assets' / 'perception_v2')
VEHICLE_COMMITMENT_WASM = _path_env('V2V_COMMITMENT_WASM', ZKP_BASE_DIR / 'vehicle_commitment_js' / 'vehicle_commitment.wasm')
VEHICLE_AUTH_WASM = _path_env('V2V_AUTH_WASM', ZKP_BASE_DIR / 'vehicle_perception_auth_js' / 'vehicle_perception_auth.wasm')
VEHICLE_AUTH_ZKEY = _path_env('V2V_AUTH_ZKEY', ZKP_BASE_DIR / 'vehicle_perception_auth_final.zkey')
VEHICLE_AUTH_VKEY = _path_env('V2V_AUTH_VKEY', ZKP_BASE_DIR / 'vehicle_perception_auth_verification_key.json')
NODE_CMD = os.environ.get('V2V_NODE_CMD', shutil.which('node') or 'node')
SNARKJS_CLI_JS = _path_env('V2V_SNARKJS_CLI_JS', PROJECT_ROOT / 'node_modules' / 'snarkjs' / 'build' / 'cli.cjs')
SNARKJS_CMD = os.environ.get('V2V_SNARKJS_CMD', shutil.which('snarkjs') or 'snarkjs')
ZKP_BACKEND_MODE = os.environ.get('V2V_ZKP_BACKEND', 'snarkjs')
REQUIRE_BINDING_GATE = _flag('V2V_REQUIRE_BINDING_GATE', True)
SNARKJS_TIMEOUT_S = float(os.environ.get('V2V_SNARKJS_TIMEOUT_S', '60'))
