pragma circom 2.0.0;
include "circomlib/circuits/poseidon.circom";

/* Public order is exactly [registered_commitment, context_hash, payload_hash].
   Private INPUT: vehicle_secret. No main outputs (which would add public signals).
   Compile with --O1. Both message hashes participate in separate non-linear
   constraints. Do NOT replace these with unused inputs or x === x.
   The message-square binding pattern also appears in Semaphore v3 circuits:
   https://github.com/semaphore-protocol/semaphore/blob/v3.15.2/packages/circuits/semaphore.circom
   This is not a circuit for LiDAR truth, SHA-256, canonicalization, or extraction.
   Run scripts/real_snarkjs_smoke.py after setup: its direct backend tests mutate
   each public input WITHOUT Python manifest gates, and must fail verification.
*/
template VehicleAuthV2() {
    signal input registered_commitment;
    signal input context_hash;
    signal input payload_hash;
    signal input vehicle_secret;
    component commitment = Poseidon(1);
    commitment.inputs[0] <== vehicle_secret;
    commitment.out === registered_commitment;
    signal contextHashSquared;
    signal payloadHashSquared;
    contextHashSquared <== context_hash * context_hash;
    payloadHashSquared <== payload_hash * payload_hash;
}
component main {public [registered_commitment, context_hash, payload_hash]} = VehicleAuthV2();
