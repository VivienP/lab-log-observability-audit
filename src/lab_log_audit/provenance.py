"""Input integrity checks used before any source archive is parsed."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


class ProvenanceError(RuntimeError):
    """Raised when an input is absent or differs from the pinned manifest."""


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    path: Path
    size: int
    sha256: str
    md5: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(
    path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    expected_md5: str | None = None,
) -> VerifiedFile:
    if not path.is_file():
        raise ProvenanceError(f"required input is missing: {path}")
    size = path.stat().st_size
    if size != expected_size:
        raise ProvenanceError(f"size mismatch for {path.name}: expected {expected_size}, got {size}")
    digest = sha256_file(path)
    if digest.lower() != expected_sha256.lower():
        raise ProvenanceError(
            f"SHA-256 mismatch for {path.name}: expected {expected_sha256}, got {digest}"
        )
    md5 = None
    if expected_md5 is not None:
        md5 = md5_file(path)
        if md5.lower() != expected_md5.lower():
            raise ProvenanceError(f"MD5 mismatch for {path.name}: expected {expected_md5}, got {md5}")
    return VerifiedFile(path=path, size=size, sha256=digest, md5=md5)

