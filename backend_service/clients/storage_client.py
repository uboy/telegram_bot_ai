"""
Storage client abstraction (S3/local/etc).
"""

from typing import Protocol, BinaryIO


class StorageClient(Protocol):
    """Минимальный контракт для клиентов хранения артефактов."""

    def save(self, path: str, data: bytes) -> None:
        ...

    def open(self, path: str) -> BinaryIO:
        ...


