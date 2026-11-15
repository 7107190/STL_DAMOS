"""Actual three-public-input Groth16 backend. NO public-key-derived HMAC binding.
Circuit statement: knowledge of secret whose Poseidon hash is registered_commitment,
with context_hash and payload_hash included in the Groth16 public statement.
SHA-256, LiDAR extraction and freshness checks are outside the circuit.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from app import config
from crypto.asset_metadata import resolve_asset_directory, zkey_header, file_hash, AUTH_NAMES
from perception.canonical import canonical_json_bytes

LOG = logging.getLogger(__name__)
FIELD = config.SNARK_FIELD_MODULUS
REAL_BACKEND = 'snarkjs-groth16-perception-v1'
MOCK_BACKEND = 'MOCK_ONLY-groth16-perception-v1'

def field_string(value):
    if (type(value) is not str or not re.fullmatch(r'0|[1-9][0-9]{0,76}', value)
            or int(value) >= FIELD):
        raise ValueError('INVALID_CANONICAL_FIELD_ELEMENT')
    return value

def validate_public_inputs(public):
    if type(public) is not list or len(public) != 3:
        raise ValueError('EXACTLY_THREE_PUBLIC_INPUTS_REQUIRED')
    return [field_string(x) for x in public]

def hash_bytes_to_field(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(part)
    return str(int.from_bytes(h.digest(), 'big') % FIELD)

def make_payload_hash(manifest):
    return hash_bytes_to_field(b'DAMOS/PERCEPTION/MANIFEST/v1\x00', canonical_json_bytes(manifest))

def make_v2v_context_hash(src_id, dst_id, nonce, session_id, issued_at, payload_hash,
                          registered_commitment='', src_tag=''):
    # Structured encoding, not ambiguous concatenation of variable-length strings.
    body = dict(src_id=src_id, src_tag=src_tag, dst_id=dst_id, nonce=nonce,
        session_id=session_id, issued_at=issued_at, payload_hash=payload_hash,
        registered_commitment=registered_commitment, schema_version=1)
    return hash_bytes_to_field(config.ZKP_CONTEXT_DOMAIN, canonical_json_bytes(body))

class Groth16Backend:
    is_mock = False
    expected_public_input_count = 3
    def compute_commitment(self, vehicle_secret):
        raise NotImplementedError
    def generate_auth_proof(self, vehicle_secret, registered_commitment, context_hash, payload_hash):
        raise NotImplementedError
    def verify_auth_proof(self, proof_obj, public_obj):
        raise NotImplementedError
    def backend_name(self):
        raise NotImplementedError

class MockGroth16Backend(Groth16Backend):
    """TEST DOUBLE ONLY, not a ZKP and not suitable for reported crypto benchmarks.
    Explicit test_only=True is mandatory. A private in-memory secret registry is
    shared ONLY when a unit-test fixture passes the SAME backend to both parties.
    """
    is_mock = True
    def __init__(self, test_only=False):
        if test_only is not True:
            raise RuntimeError('MOCK_ONLY: explicit test_only=True required')
        LOG.warning('MOCK_ONLY: unit-test backend; no cryptographic result claims')
        self._secrets = {}
        self.verify_calls = 0
    def backend_name(self):
        return MOCK_BACKEND
    def compute_commitment(self, vehicle_secret):
        field_string(vehicle_secret)
        result = hash_bytes_to_field(b'MOCK_ONLY/commitment/', vehicle_secret.encode('ascii'))
        self._secrets[result] = vehicle_secret
        return result
    def _digest(self, secret, public):
        return hashlib.sha256(b'MOCK_ONLY/proof/' + secret.encode('ascii') + b'\x00' + canonical_json_bytes(public)).hexdigest()
    def generate_auth_proof(self, vehicle_secret, registered_commitment, context_hash, payload_hash):
        start = time.perf_counter()
        if self.compute_commitment(vehicle_secret) != registered_commitment:
            raise ValueError('WRONG_SECRET')
        public = validate_public_inputs([registered_commitment, context_hash, payload_hash])
        proof = dict(backend=MOCK_BACKEND, proof_hash=self._digest(vehicle_secret, public))
        return proof, public, {'prove_total_ms': (time.perf_counter()-start)*1000, 'result_class': 'MOCK_ONLY'}
    def verify_auth_proof(self, proof_obj, public_obj):
        start = time.perf_counter()
        self.verify_calls += 1
        try:
            validate_public_inputs(public_obj)
            secret = self._secrets.get(public_obj[0])
            ok = bool(secret and type(proof_obj) is dict and
                proof_obj == dict(backend=MOCK_BACKEND, proof_hash=self._digest(secret, public_obj)))
        except (ValueError, TypeError):
            ok = False
        return ok, {'verify_total_ms': (time.perf_counter()-start)*1000, 'result_class': 'MOCK_ONLY'}

class SnarkjsCommandError(RuntimeError):
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr
        super().__init__('SNARKJS_FAILED: ' + (stdout + stderr)[-4000:])


def _clean_cli_output(text):
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', text)


def _is_crypto_rejection(text):
    text = _clean_cli_output(text).lower()
    return any(marker in text for marker in (
        'invalid proof', 'proof commitments are not valid', 'public inputs are not valid'))


class SnarkjsGroth16Backend(Groth16Backend):
    def __init__(self, base_dir=None, command=None, require_gate=None):
        base = resolve_asset_directory(base_dir) if base_dir else None
        # A user may keep V2V_ZKP_BASE_DIR pointing to the original legacy parent.
        # In that case resolve only the explicitly rebuilt perception_v2 child.
        if base is None:
            resolved = resolve_asset_directory(config.ZKP_BASE_DIR)
            if resolved != config.ZKP_BASE_DIR:
                if any(os.environ.get(k) for k in ('V2V_COMMITMENT_WASM', 'V2V_AUTH_WASM',
                                                  'V2V_AUTH_ZKEY', 'V2V_AUTH_VKEY')):
                    raise ValueError('MIXED_LEGACY_ASSET_OVERRIDES: clear old V2V_AUTH_* and V2V_COMMITMENT_WASM')
                base = resolved
        self.commitment_wasm = base / 'vehicle_commitment_js' / 'vehicle_commitment.wasm' if base else config.VEHICLE_COMMITMENT_WASM
        self.auth_wasm = base / 'vehicle_perception_auth_js' / 'vehicle_perception_auth.wasm' if base else config.VEHICLE_AUTH_WASM
        self.auth_zkey = base / 'vehicle_perception_auth_final.zkey' if base else config.VEHICLE_AUTH_ZKEY
        self.auth_vkey = base / 'vehicle_perception_auth_verification_key.json' if base else config.VEHICLE_AUTH_VKEY
        self.command = list(command) if command is not None else self._resolve_command()
        self._ensure_assets_exist()
        self._check_activation_gate(config.REQUIRE_BINDING_GATE if require_gate is None else require_gate)
        LOG.info('REAL_GROTH16: snarkjs three-public-input backend; mock fallback disabled')
    def backend_name(self):
        return REAL_BACKEND
    @staticmethod
    def _resolve_command():
        cli = config.SNARKJS_CLI_JS
        node = shutil.which(config.NODE_CMD) or (str(Path(config.NODE_CMD).resolve()) if Path(config.NODE_CMD).is_file() else None)
        if cli.is_file() and node:
            return [node, str(cli.resolve())]
        executable = shutil.which(config.SNARKJS_CMD)
        if not executable and Path(config.SNARKJS_CMD).is_file():
            executable = str(Path(config.SNARKJS_CMD).resolve())
        if executable:
            p = Path(executable)
            # Global Windows npm shim: invoke its real JS target with node, shell=False.
            if p.suffix.lower() in ('.cmd', '.bat', '.ps1'):
                target = p.parent / 'node_modules' / 'snarkjs' / 'build' / 'cli.cjs'
                if target.is_file() and node:
                    return [node, str(target.resolve())]
            elif p.suffix.lower() in ('.js', '.cjs', '.mjs') and node:
                return [node, str(p.resolve())]
            else:
                return [str(p.resolve())]
        raise FileNotFoundError('SNARKJS_NOT_FOUND: run npm install from project root or set V2V_SNARKJS_CLI_JS and V2V_NODE_CMD')
    def _ensure_assets_exist(self):
        missing = [str(p) for p in (self.commitment_wasm, self.auth_wasm, self.auth_zkey, self.auth_vkey) if not p.is_file()]
        if missing:
            raise FileNotFoundError('GROTH16_ASSETS_MISSING (no mock fallback): ' + ', '.join(missing))
        vk = json.loads(self.auth_vkey.read_text(encoding='utf-8-sig'))
        self.validate_verification_key(vk)
        zk = zkey_header(self.auth_zkey)
        if zk['nPublic'] != 3:
            raise ValueError('V1_ZKEY_REJECTED: proving key must have nPublic=3; renaming or editing JSON is not an upgrade')
    def _check_activation_gate(self, required):
        if not required:
            return
        path = self.auth_vkey.parent / 'activation_gate.json'
        if not path.is_file():
            raise ValueError('REAL_BINDING_GATE_MISSING: run scripts.connect_groth16')
        gate = json.loads(path.read_text(encoding='utf-8-sig'))
        if (gate.get('status') != 'PASS' or gate.get('result_class') != 'REAL_GROTH16'
                or gate.get('public_input_count') != 3):
            raise ValueError('REAL_BINDING_GATE_NOT_PASSED')
        actual = {'commitment_wasm': self.commitment_wasm, 'auth_wasm': self.auth_wasm,
                  'auth_zkey': self.auth_zkey, 'verification_key': self.auth_vkey}
        expected = gate.get('asset_sha256', {})
        if set(expected) != set(actual) or any(file_hash(v) != expected[k] for k, v in actual.items()):
            raise ValueError('ASSETS_CHANGED_AFTER_REAL_BINDING_GATE')
        # This receipt detects accidental asset mixing, not a malicious local administrator.

    @staticmethod
    def validate_verification_key(vk):
        if type(vk) is not dict or vk.get('protocol') != 'groth16' or vk.get('curve') not in ('bn128','bn254'):
            raise ValueError('UNSUPPORTED_VERIFICATION_KEY')
        if type(vk.get('nPublic')) is not int or vk['nPublic'] != 3:
            raise ValueError('V1_ASSETS_REJECTED: verification key must have nPublic=3')
        ic = vk.get('IC')
        if type(ic) is not list or len(ic) != 4:
            raise ValueError('INVALID_VERIFICATION_KEY_IC')
        for point in ic:
            if type(point) is not list or len(point) != 3:
                raise ValueError('INVALID_VERIFICATION_KEY_POINT')
            if not all(type(x) is str and x.isascii() and x.isdecimal() for x in point):
                raise ValueError('INVALID_VERIFICATION_KEY_POINT')
        if any(int(point[2]) == 0 for point in ic[1:]):
            raise ValueError('UNBOUND_PUBLIC_INPUT_IC_INFINITY')
        if len(set(tuple(p) for p in ic[1:])) != 3:
            raise ValueError('DUPLICATE_PUBLIC_INPUT_IC')
    def _run(self, args, cwd):
        command = self.command + [str(x) for x in args]
        try:
            res = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=config.SNARKJS_TIMEOUT_S, shell=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError('SNARKJS_TIMEOUT')
        if res.returncode != 0:
            raise SnarkjsCommandError(res.returncode, res.stdout, res.stderr)
        return res
    def compute_commitment(self, vehicle_secret):
        field_string(vehicle_secret)
        with tempfile.TemporaryDirectory(prefix='damos_commit_') as tmp:
            p = Path(tmp)
            (p/'input.json').write_text(json.dumps({'vehicle_secret': vehicle_secret}),encoding='utf-8')
            self._run(['wtns','calculate',self.commitment_wasm,p/'input.json',p/'witness.wtns'],p)
            self._run(['wtns','export','json',p/'witness.wtns',p/'witness.json'],p)
            witness = json.loads((p/'witness.json').read_text(encoding='utf-8'))
            return field_string(witness[1])
    def generate_auth_proof(self, vehicle_secret, registered_commitment, context_hash, payload_hash):
        field_string(vehicle_secret)
        public_expected = validate_public_inputs([registered_commitment, context_hash, payload_hash])
        with tempfile.TemporaryDirectory(prefix='damos_prove_') as tmp:
            p = Path(tmp)
            data = dict(vehicle_secret=vehicle_secret, registered_commitment=registered_commitment,
                        context_hash=context_hash, payload_hash=payload_hash)
            start = time.perf_counter()
            (p/'input.json').write_text(json.dumps(data), encoding='utf-8')
            self._run(['groth16','fullprove',p/'input.json',self.auth_wasm,self.auth_zkey,p/'proof.json',p/'public.json'],p)
            proof = json.loads((p/'proof.json').read_text(encoding='utf-8'))
            public = json.loads((p/'public.json').read_text(encoding='utf-8'))
            # Do not fabricate/append public inputs. Require actual witness public.json.
            if validate_public_inputs(public) != public_expected:
                raise RuntimeError('ACTUAL_PUBLIC_JSON_MISMATCH: check circuit order/assets')
            return proof, public, {'prove_total_ms': (time.perf_counter()-start)*1000, 'result_class': 'REAL_GROTH16'}
    def verify_auth_proof(self, proof_obj, public_obj):
        start = time.perf_counter()
        error, status, ok = '', 'OPERATIONAL_ERROR', False
        try:
            validate_public_inputs(public_obj)
            if type(proof_obj) is not dict or proof_obj.get('protocol') != 'groth16' or proof_obj.get('curve') != 'bn128':
                raise ValueError('INVALID_PROOF_FORMAT')
            if set(proof_obj) != {'pi_a','pi_b','pi_c','protocol','curve'}:
                raise ValueError('UNKNOWN_PROOF_FIELDS_OR_LEGACY_BUNDLE')
        except (ValueError, TypeError) as exc:
            status, error = 'INVALID_INPUT', str(exc)
        else:
            try:
                with tempfile.TemporaryDirectory(prefix='damos_verify_') as tmp:
                    p = Path(tmp)
                    (p/'proof.json').write_text(json.dumps(proof_obj),encoding='utf-8')
                    (p/'public.json').write_text(json.dumps(public_obj),encoding='utf-8')
                    res = self._run(['groth16','verify',self.auth_vkey,p/'public.json',p/'proof.json'],p)
                    text = _clean_cli_output(res.stdout + res.stderr)
                    if _is_crypto_rejection(text):
                        status = 'INVALID_PROOF'
                    elif re.search(r'(?:^|\s)OK!\s*$', text, re.MULTILINE):
                        ok, status = True, 'VERIFIED'
                    else:
                        error = 'UNRECOGNIZED_SNARKJS_VERIFY_OUTPUT:' + text[-1000:]
            except SnarkjsCommandError as exc:
                error = str(exc)
                status = 'INVALID_PROOF' if _is_crypto_rejection(exc.stdout + exc.stderr) else 'OPERATIONAL_ERROR'
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                error, status = str(exc), 'OPERATIONAL_ERROR'
        return ok, {'verify_total_ms': (time.perf_counter()-start)*1000,
                    'result_class': 'REAL_GROTH16', 'verification_status': status, 'error': error}

def create_groth16_backend(mode=None, test_only=False):
    mode = (mode or config.ZKP_BACKEND_MODE).lower()
    if mode == 'mock':
        return MockGroth16Backend(test_only=test_only)
    if mode in ('auto','snarkjs'):
        return SnarkjsGroth16Backend()  # auto deliberately no longer downgrades security
    raise ValueError('UNKNOWN_ZKP_BACKEND:' + mode)

class VehicleGroth16Context:
    def __init__(self, tag, backend=None, vehicle_secret=None):
        self.tag = tag
        self.backend = backend if backend is not None else create_groth16_backend()
        self.vehicle_secret = vehicle_secret if vehicle_secret is not None else str(secrets.randbelow(FIELD-1)+1)
        field_string(self.vehicle_secret)
        self.registered_commitment = self.backend.compute_commitment(self.vehicle_secret)
    @property
    def backend_id(self):
        return self.backend.backend_name()
    def generate_packet_proof(self, manifest, src_id, dst_id, nonce, session_id, issued_at):
        payload_hash = make_payload_hash(manifest)
        context_hash = make_v2v_context_hash(src_id, dst_id, nonce, session_id, issued_at,
            payload_hash, self.registered_commitment, manifest['src_tag'])
        proof, public, timing = self.backend.generate_auth_proof(self.vehicle_secret,
            self.registered_commitment, context_hash, payload_hash)
        if validate_public_inputs(public) != [self.registered_commitment, context_hash, payload_hash]:
            raise RuntimeError('PUBLIC_INPUT_MISMATCH')
        return payload_hash, context_hash, proof, public, timing
