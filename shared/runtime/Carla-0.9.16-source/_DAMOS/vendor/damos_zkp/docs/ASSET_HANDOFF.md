# Groth16 asset handoff

The generated Groth16 runtime assets in `assets/perception_v2/` may be handed to the integrator together with the code.

Runtime-distributable artifacts in this package:

- `vehicle_commitment_js/vehicle_commitment.wasm`
- `vehicle_perception_auth_js/vehicle_perception_auth.wasm`
- `vehicle_perception_auth_final.zkey` (proving key)
- `vehicle_perception_auth_verification_key.json`
- `activation_gate.json`

These are not device secrets. The `zkey` is required by the prover and the verification key is public verification material.

**Do not hand over an operational `vehicle_secret` / anchor secret in source code, logs, JSON fixtures, or environment dumps.** Provision that secret separately. Receivers only need the registered commitment.

The current assets were generated under the recorded setup class:

`PINNED_PUBLIC_PTAU_NO_LOCAL_TRANSCRIPT_REVERIFY_LOCAL_PHASE2_TRUST_NOT_AUDITED`

The pinned PTAU artifact digest matched the published digest, the generated zkey passed snarkjs crypto verification, and a real three-public-input binding smoke passed. However, the public PTAU ceremony transcript was not reverified locally in that setup run and the local phase-2 contribution was not independently audited. Preserve this limitation in any report. For a production or mutually distrustful deployment, perform an organization-approved setup ceremony and regenerate the circuit-specific proving/verification keys.
