# DAMOS Security Handoff

Minimal handoff package for the central main-computer -> vehicle / delivery-robot / humanoid authenticated perception broadcast.

This package deliberately excludes CARLA driving, V2V distance triggers, BasicAgent, old attack runners, PTAU setup scripts, legacy code, reports, and node_modules.

## What it receives

A **fused object list** from the integrator's perception/fusion module.

## What it outputs

One strict JSON byte string containing:

- fused perception payload
- object-unit SHA-256 Merkle commitment
- authenticated manifest
- anchor epoch / sequence / timestamp / recipient set
- Groth16 proof with exactly three public inputs:
  1. `registered_commitment`
  2. `context_hash`
  3. `payload_hash`

The same byte string can be distributed to all recipients listed in `recipient_ids`.

## Requirements

- Python 3.7+
- Node.js 18+
- snarkjs 0.7.6 (`npm ci`)

No CARLA installation is needed for the security module itself.

## Setup

```bash
npm ci
python check_assets.py
```

Windows PowerShell:

```powershell
npm ci
py -3.7 .\check_assets.py
```

## Real Groth16 smoke

```bash
python examples/real_roundtrip.py
```

or on Windows:

```powershell
$env:DAMOS_ANCHOR_SECRET = "<provisioned field element>"
py -3.7 .\examples\real_roundtrip.py
```

For a quick code-path test without cryptographic claims:

```bash
python -m unittest tests.test_handoff_mock -v
```

## Integration

See `docs/INTEGRATION_API.md` and `examples/integration_skeleton.py`.

## Important trust statement

See `docs/ASSET_HANDOFF.md`. The Groth16 runtime assets are included and may be distributed. Device/anchor secrets are intentionally not included.
