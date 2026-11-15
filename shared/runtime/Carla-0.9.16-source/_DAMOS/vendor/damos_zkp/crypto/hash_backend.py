"""Generic hash backend retained from original. Perception protocol ONLY permits SHA-256.
The optional legacy Poseidon byte wrapper below has NOT been validated against
circomlib and is NOT used for commitments or Groth16 identity derivation here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import struct
from typing import List


# BN254 스칼라 필드 모듈러스
_BN254_FIELD = (
    21888242871839275222246405745257275088548364400416034343698204186575808495617
)


class HashBackend(ABC):
    """
    추상 해시 백엔드 인터페이스.

    Generic hash + name interface. The perception protocol separately enforces
    its SHA-256 allowlist; implementing this interface does not authorize a backend.
    """

    @abstractmethod
    def hash(self, data: bytes) -> bytes:
        """data 의 고정 길이 다이제스트 반환."""
        ...

    @abstractmethod
    def name(self) -> str:
        """manifest에 기록되는 백엔드 식별자."""
        ...


# =============================================================================
# SHA-256 백엔드
# =============================================================================
class SHA256HashBackend(HashBackend):
    """
    SHA-256 backend used by the current perception commitments.
    Hashing is outside the Groth16 circuit; no circuit-constraint cost is claimed.
    """

    def hash(self, data: bytes) -> bytes:
        return hashlib.sha256(data).digest()

    def name(self) -> str:
        return "SHA-256"


# =============================================================================
# Poseidon 백엔드 — [항목 4 구현]
# =============================================================================
class PoseidonHashBackend(HashBackend):
    """
    Legacy optional wrapper, NOT circomlib-validated and NOT used by this protocol.

    [항목 4 구현 내용]
    - poseidon-hash PyPI 패키지 사용 (pip install poseidon-hash)
    - Historical parameter assumption is unverified; do not use for identity commitments.
    - 임의 길이 bytes → 청크 분할 → 필드 원소 → Poseidon 해시 → 32바이트 반환

    bytes → field element 변환 방식:
      1. data 를 31바이트 청크로 분할 (BN254 필드 오버플로 방지)
      2. 각 청크를 big-endian 정수로 변환
      3. Poseidon(청크들) 계산
      4. 결과 필드 원소를 32바이트 big-endian으로 직렬화

    No verified constraint count or circuit compatibility is claimed for this wrapper.

    주의: Merkle 트리와 번들 루트 계산에 이 백엔드를 사용할 경우,
    circom 회로 내 Poseidon과 동일한 파라미터를 사용해야
    Python 계산 결과와 회로 계산 결과가 일치함.

    poseidon-hash 미설치 시: ImportError 발생 → 아래 설치 안내 출력
    """

    _CHUNK_SIZE = 31  # BN254 필드 원소 1개에 안전하게 담을 수 있는 최대 바이트 수

    def __init__(self) -> None:
        self._poseidon_fn = self._load_poseidon()

    @staticmethod
    def _load_poseidon():
        """
        poseidon-hash 패키지 로드.
        미설치 시 명확한 에러 메시지 제공.
        """
        try:
            from poseidon import poseidon_hash
            return poseidon_hash
        except ImportError:
            raise ImportError(
                "\n\n[PoseidonHashBackend] poseidon-hash 패키지가 설치되어 있지 않습니다.\n"
                "설치 명령:\n"
                "  pip install poseidon-hash\n\n"
                "또는 SHA256HashBackend를 계속 사용하려면:\n"
                "  hash_backend = SHA256HashBackend()\n"
            )

    def _bytes_to_field_elements(self, data: bytes) -> List[int]:
        """
        임의 길이 bytes를 BN254 필드 원소 목록으로 변환.
        31바이트 청크 단위로 분할하여 필드 오버플로 방지.
        빈 데이터: [0] 반환 (empty leaf 처리)
        """
        if not data:
            return [0]
        elements = []
        for i in range(0, len(data), self._CHUNK_SIZE):
            chunk = data[i : i + self._CHUNK_SIZE]
            elements.append(int.from_bytes(chunk, "big"))
        return elements

    def _poseidon_of_elements(self, elements: List[int]) -> int:
        """
        필드 원소 목록에 대해 Poseidon 해시 계산.
        입력이 여러 개일 때: 순차적으로 2개씩 결합 (Merkle-Damgård 방식).
        """
        if len(elements) == 1:
            return self._poseidon_fn([elements[0]])
        # 두 원소씩 결합하며 축약
        acc = elements[0]
        for elem in elements[1:]:
            acc = self._poseidon_fn([acc, elem])
        return acc

    def hash(self, data: bytes) -> bytes:
        """
        bytes → Poseidon 해시 → 32바이트 반환.

        반환 형식: 32바이트 big-endian (SHA-256 반환 형식과 동일 인터페이스)
        값 범위: [0, BN254_FIELD_MODULUS)
        """
        elements = self._bytes_to_field_elements(data)
        result   = self._poseidon_of_elements(elements)
        # 필드 원소를 32바이트 big-endian으로 직렬화
        return result.to_bytes(32, "big")

    def name(self) -> str:
        return "Poseidon-BN254"


# =============================================================================
# 모듈 기본값 및 팩토리
# =============================================================================

# 전역 기본 백엔드 — 어디서든 이 인스턴스를 import해서 사용
DEFAULT_HASH_BACKEND: HashBackend = SHA256HashBackend()


def get_backend_by_name(name: str) -> HashBackend:
    """
    manifest에 저장된 백엔드 이름으로 인스턴스 반환.
    송신측과 수신측이 동일한 백엔드를 사용함을 보장.
    """
    if name == "SHA-256":
        return SHA256HashBackend()
    if name in ("Poseidon", "Poseidon-BN254"):
        return PoseidonHashBackend()
    raise ValueError(
        f"알 수 없는 해시 백엔드: '{name}'. "
        f"지원: 'SHA-256', 'Poseidon-BN254'."
    )


def get_zk_optimized_backend() -> HashBackend:
    """
    Inactive legacy convenience API; its name is not a security/compatibility claim.
    poseidon-hash 설치 시 PoseidonHashBackend, 미설치 시 SHA256 폴백.
    """
    try:
        return PoseidonHashBackend()
    except ImportError:
        print(
            "[hash_backend] poseidon-hash 미설치 → SHA256 폴백 사용. "
            "LEGACY_ONLY: not used or permitted by the current perception protocol"
        )
        return SHA256HashBackend()
