pragma circom 2.0.0;
include "circomlib/circuits/poseidon.circom";
// Helper witness[1] equals the SAME Poseidon(1) used in authentication.
template VehicleCommitmentV2() {
    signal input vehicle_secret;
    signal output registered_commitment;
    component hasher = Poseidon(1);
    hasher.inputs[0] <== vehicle_secret;
    registered_commitment <== hasher.out;
}
component main = VehicleCommitmentV2();
