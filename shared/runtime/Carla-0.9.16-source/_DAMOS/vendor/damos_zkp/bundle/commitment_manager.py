"""Object-unit SHA-256 Merkle commitments, shared with the receiver recomputation."""
from crypto.hash_backend import SHA256HashBackend
from crypto.merkle import MerkleTree
from perception.canonical import canonical_json_bytes
from perception.schema import normalize_payload

DOMAIN_OBJECT = b'DAMOS/PERCEPTION/OBJECT/v1\x00'
DOMAIN_NODE = b'DAMOS/PERCEPTION/NODE/v1\x00'
DOMAIN_EMPTY = b'DAMOS/PERCEPTION/EMPTY/v1\x00'
DOMAIN_FRAME = b'DAMOS/PERCEPTION/FRAME/v1\x00'
HASH_BACKEND = 'SHA-256'

class PerceptionCommitmentManager:
    def __init__(self, payload):
        self.payload = normalize_payload(payload)
        self.backend = SHA256HashBackend()
        self.leaves = [self.backend.hash(DOMAIN_OBJECT + canonical_json_bytes(obj))
                       for obj in self.payload['objects']]
        self.tree = MerkleTree.from_leaf_hashes(self.leaves, self.backend,
            node_domain=DOMAIN_NODE, empty_root=self.backend.hash(DOMAIN_EMPTY))
        self.perception_root = self.tree.root_hex
        self.object_count = len(self.payload['objects'])
        meta = {k: v for k, v in self.payload.items() if k != 'objects'}
        meta.update(perception_root=self.perception_root,
                    object_count=self.object_count, hash_backend=HASH_BACKEND)
        self.frame_commitment = self.backend.hash(DOMAIN_FRAME + canonical_json_bytes(meta)).hex()

def recompute_perception(payload):
    return PerceptionCommitmentManager(payload)
