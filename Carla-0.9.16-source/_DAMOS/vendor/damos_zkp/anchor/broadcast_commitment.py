"""Object-unit Merkle commitment for anchor broadcasts.

Reuses crypto.merkle.MerkleTree.from_leaf_hashes and the SAME object leaf domain
as bundle.commitment_manager, so an anchor leaf and a modality leaf for identical
object values collide by construction. The FRAME domain is deliberately different:
an anchor frame commitment must never be mistaken for a V2V sensor frame
commitment, and vice versa.
"""
from anchor.broadcast_schema import normalize_broadcast_payload
from bundle.commitment_manager import DOMAIN_OBJECT, DOMAIN_NODE, DOMAIN_EMPTY, HASH_BACKEND
from crypto.hash_backend import SHA256HashBackend
from crypto.merkle import MerkleTree
from perception.canonical import canonical_json_bytes

DOMAIN_ANCHOR_FRAME = b'DAMOS/ANCHOR/FRAME/v1\x00'


class AnchorCommitmentManager:
    def __init__(self, payload):
        self.payload = normalize_broadcast_payload(payload)
        self.backend = SHA256HashBackend()
        self.leaves = [self.backend.hash(DOMAIN_OBJECT + canonical_json_bytes(obj))
                       for obj in self.payload['objects']]
        self.tree = MerkleTree.from_leaf_hashes(
            self.leaves, self.backend,
            node_domain=DOMAIN_NODE, empty_root=self.backend.hash(DOMAIN_EMPTY))
        self.perception_root = self.tree.root_hex
        self.object_count = len(self.payload['objects'])
        meta = {k: v for k, v in self.payload.items() if k != 'objects'}
        meta.update(perception_root=self.perception_root,
                    object_count=self.object_count, hash_backend=HASH_BACKEND)
        self.frame_commitment = self.backend.hash(
            DOMAIN_ANCHOR_FRAME + canonical_json_bytes(meta)).hex()

    def object_proof(self, index):
        """Inclusion proof for one object, for a modality that wants to relay a
        single object without the full list. Membership only; it does not show
        that the object list is complete."""
        return self.tree.get_proof(index)


def recompute_broadcast(payload):
    return AnchorCommitmentManager(payload)
