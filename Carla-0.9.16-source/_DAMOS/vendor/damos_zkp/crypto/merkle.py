"""Reusable binary Merkle tree for byte chunks or already-hashed object leaves.
The current perception protocol uses from_leaf_hashes(), domain-separated nodes,
an explicit empty-frame hash, sorted unique object IDs and a committed object count.
Odd-sized levels duplicate their final node. The old bytes constructor remains
available to independent legacy callers and uses its original empty-byte leaf.
Inclusion proofs establish membership against an independently trusted root.
A bad recomputed root alone DOES NOT localize a corrupted object/chunk, and this
module makes no image-slot or exact byte-range tamper-localization claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from crypto.hash_backend import HashBackend, DEFAULT_HASH_BACKEND


# =============================================================================
# MERKLE PROOF 구조체
# =============================================================================
@dataclass
class MerkleProof:
    """
    특정 leaf(청크)에 대한 Merkle 포함 증명.

    Attributes
    ----------
    leaf_index   : 증명할 leaf 의 인덱스
    leaf_hash    : 해당 leaf 의 해시값
    path         : [(sibling_hash, 'left'|'right'), ...] — root 방향으로 정렬
    root         : 이 증명이 속한 트리의 루트
    chunk_size   : 청크 크기 (송수신 간 일치 필요)
    backend_name : 사용된 해시 백엔드 이름
    """
    leaf_index:   int
    leaf_hash:    bytes
    path:         List[Tuple[bytes, str]]  # (sibling_hash, 'left'|'right')
    root:         bytes
    chunk_size:   int  = 4096
    backend_name: str  = "SHA-256"
    node_domain: bytes = b""

    def verify(
        self,
        expected_root: bytes,
        backend: HashBackend = DEFAULT_HASH_BACKEND,
    ) -> bool:
        """
        이 proof가 expected_root 에 대해 유효한지 검증.

        Parameters
        ----------
        expected_root : 검증할 루트 해시 (bytes)
        backend       : 송신측과 동일한 해시 백엔드

        Returns
        -------
        True iff proof 경로를 따라 계산한 루트 == expected_root
        """
        current = self.leaf_hash
        for sibling_hash, direction in self.path:
            if direction == "right":
                # current 가 왼쪽, sibling 이 오른쪽
                current = backend.hash(self.node_domain + current + sibling_hash)
            elif direction == "left":
                # sibling 이 왼쪽, current 가 오른쪽
                current = backend.hash(self.node_domain + sibling_hash + current)
            else:
                return False
        return current == expected_root

    def to_dict(self) -> dict:
        """직렬화 — 패킷/로그 포함용."""
        return {
            "leaf_index":   self.leaf_index,
            "leaf_hash":    self.leaf_hash.hex(),
            "path":         [(h.hex(), d) for h, d in self.path],
            "root":         self.root.hex(),
            "chunk_size":   self.chunk_size,
            "backend_name": self.backend_name,
            "node_domain": self.node_domain.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict, backend: HashBackend = DEFAULT_HASH_BACKEND) -> "MerkleProof":
        """역직렬화."""
        return cls(
            leaf_index=d["leaf_index"],
            leaf_hash=bytes.fromhex(d["leaf_hash"]),
            path=[(bytes.fromhex(h), direction) for h, direction in d["path"]],
            root=bytes.fromhex(d["root"]),
            chunk_size=d.get("chunk_size", 4096),
            backend_name=d.get("backend_name", "SHA-256"),
            node_domain=bytes.fromhex(d.get("node_domain", "")),
        )


# =============================================================================
# MERKLE TREE
# =============================================================================
class MerkleTree:
    """
    data bytes 로부터 Merkle 트리 구성.

    Parameters
    ----------
    data       : 커밋할 원본 bytes (기존 범용 byte API)
    chunk_size : leaf 당 바이트 수. 송수신 간 반드시 일치.
    backend    : 해시 함수. 송수신 간 반드시 일치.
    """

    def __init__(
        self,
        data: bytes,
        chunk_size: int = 4096,
        backend: HashBackend = DEFAULT_HASH_BACKEND,
    ):
        if type(chunk_size) is not int or chunk_size <= 0:
            raise ValueError("invalid chunk_size")
        self.node_domain = b""
        self.chunk_size = chunk_size
        self.backend    = backend

        # 청크 분할 (빈 입력 보호)
        if not data:
            chunks = [b""]
        else:
            chunks = [
                data[i : i + chunk_size]
                for i in range(0, len(data), chunk_size)
            ]

        # [항목 5] leaf 목록 저장 — proof 생성 시 재사용
        self._leaves: List[bytes] = [backend.hash(c) for c in chunks]
        self._root:   bytes       = self._build(list(self._leaves))

    @classmethod
    def from_leaf_hashes(cls, leaves, backend=DEFAULT_HASH_BACKEND,
                         node_domain=b"", empty_root=None):
        """Prehashed object leaves. Empty frames have a dedicated domain hash.
        Duplicate-last padding is performed at EVERY odd-sized level.
        The caller commits count and rejects duplicate object IDs.
        """
        tree = cls.__new__(cls)
        tree.backend, tree.node_domain, tree.chunk_size = backend, node_domain, 0
        tree._leaves = list(leaves)
        if any(type(h) is not bytes or len(h) != 32 for h in tree._leaves):
            raise ValueError("leaf hashes must be 32 bytes")
        if not tree._leaves:
            if type(empty_root) is not bytes or len(empty_root) != 32:
                raise ValueError("explicit 32-byte empty_root required")
            tree._root = empty_root
        else:
            tree._root = tree._build(list(tree._leaves))
        return tree

    # ── 공개 프로퍼티 ──────────────────────────────────────────────────────────

    @property
    def root(self) -> bytes:
        """원시 32바이트 루트 다이제스트."""
        return self._root

    @property
    def root_hex(self) -> str:
        """16진수 루트 다이제스트 (SHA-256: 64자)."""
        return self._root.hex()

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    # ── [항목 5] Merkle Proof 생성 ─────────────────────────────────────────────

    def get_proof(self, leaf_index: int) -> MerkleProof:
        """
        특정 leaf 에 대한 Merkle 포함 증명 반환.

        Parameters
        ----------
        leaf_index : 증명할 leaf 의 인덱스 (0-based)

        Returns
        -------
        MerkleProof — path 는 leaf → root 방향으로 정렬

        사용 예:
            tree = MerkleTree(payload_bytes)
            proof = tree.get_proof(0)          # 첫 번째 청크 증명
            assert proof.verify(bytes.fromhex(tree.root_hex), backend)
        """
        if not (0 <= leaf_index < len(self._leaves)):
            raise IndexError(
                f"leaf_index {leaf_index} 범위 초과 "
                f"(leaf 수: {len(self._leaves)})"
            )

        path:  List[Tuple[bytes, str]] = []
        nodes: List[bytes]             = list(self._leaves)
        idx:   int                     = leaf_index

        while len(nodes) > 1:
            # 홀수 노드 패딩
            if len(nodes) % 2 == 1:
                nodes = nodes + [nodes[-1]]

            sibling_idx = idx ^ 1  # XOR 1: 형제 노드 인덱스
            if idx % 2 == 0:
                # current 가 왼쪽 → 형제가 오른쪽
                direction = "right"
            else:
                # current 가 오른쪽 → 형제가 왼쪽
                direction = "left"

            path.append((nodes[sibling_idx], direction))

            # 한 레벨 올라가기
            nodes = [
                self.backend.hash(self.node_domain + nodes[i] + nodes[i + 1])
                for i in range(0, len(nodes), 2)
            ]
            idx //= 2

        return MerkleProof(
            leaf_index=leaf_index,
            leaf_hash=self._leaves[leaf_index],
            path=path,
            root=self._root,
            chunk_size=self.chunk_size,
            backend_name=self.backend.name(),
            node_domain=self.node_domain,
        )

    def get_all_proofs(self) -> List[MerkleProof]:
        """모든 leaf 에 대한 proof 목록 반환 (디버깅/감사 용도)."""
        return [self.get_proof(i) for i in range(len(self._leaves))]

    # ── 내부 트리 구성 ─────────────────────────────────────────────────────────

    def _build(self, nodes: List[bytes]) -> bytes:
        """노드 목록을 단일 루트 해시로 재귀 환원."""
        if len(nodes) == 1:
            return nodes[0]
        if len(nodes) % 2 == 1:
            nodes = nodes + [nodes[-1]]
        parents: List[bytes] = [
            self.backend.hash(self.node_domain + nodes[i] + nodes[i + 1])
            for i in range(0, len(nodes), 2)
        ]
        return self._build(parents)


# =============================================================================
# 편의 함수
# =============================================================================

def compute_merkle_root(
    data: bytes,
    chunk_size: int = 4096,
    backend: HashBackend = DEFAULT_HASH_BACKEND,
) -> str:
    """
    bytes 로부터 Merkle root hex 직접 계산.
    송신측(commitment)과 수신측(recomputation) 양쪽에서 사용.
    """
    return MerkleTree(data, chunk_size=chunk_size, backend=backend).root_hex


def verify_chunk_proof(
    chunk_data: bytes,
    proof: MerkleProof,
    expected_root_hex: str,
    backend: HashBackend = DEFAULT_HASH_BACKEND,
) -> bool:
    """
    단일 청크 데이터와 MerkleProof 로 무결성 검증.

    Parameters
    ----------
    chunk_data       : 검증할 원본 청크 bytes
    proof            : get_proof() 로 생성한 MerkleProof
    expected_root_hex: 기대하는 Merkle root (16진수 문자열)
    backend          : 송신측과 동일한 해시 백엔드

    Returns
    -------
    True iff chunk_data 가 expected_root 에 속한 정상 청크임
    """
    # 청크 hash 재계산 후 proof 검증
    recomputed_leaf = backend.hash(chunk_data)
    if recomputed_leaf != proof.leaf_hash:
        return False
    return proof.verify(bytes.fromhex(expected_root_hex), backend)
