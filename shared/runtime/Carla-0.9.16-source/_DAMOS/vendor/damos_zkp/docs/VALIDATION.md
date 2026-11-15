# Validation summary

The handoff package was validated after extraction from the two supplied archives.

## Source checks

- Groth16 proving key binary header: `nPublic=3`
- Verification key: `nPublic=3`
- Runtime asset SHA-256 values match `assets/perception_v2/ASSET_PROVENANCE.json`
- No user-specific Windows path is required by the handoff package

## Executed tests

1. `python check_assets.py` -> PASS
2. `python -m unittest tests.test_handoff_mock -v` -> 4/4 PASS
3. Real snarkjs/Groth16 round trip using the included WASM/ZKEY/VKEY -> PASS for all three recipients

Observed on the validation host for one 2-object payload (illustrative only, not a Windows/Ubuntu benchmark):

- proof generation: about 400 ms
- proof verification: about 299-328 ms per receiver
- wire size: about 3.3 KB

These numbers depend on CPU, OS, Node.js/snarkjs process overhead, and payload size. Re-measure on the integration machine before reporting performance.

The real validation used snarkjs 0.7.6 from the supplied source project's installed `node_modules`; the handoff ZIP intentionally excludes `node_modules`. The recipient should run `npm ci`.
