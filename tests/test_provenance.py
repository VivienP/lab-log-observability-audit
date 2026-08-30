from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lab_log_audit.provenance import ProvenanceError, verify_file


def test_verify_file_checks_size_and_sha256(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"source bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    verified = verify_file(source, expected_size=12, expected_sha256=digest)

    assert verified.sha256 == digest
    assert verified.size == 12


def test_verify_file_rejects_missing_or_modified_input(tmp_path: Path) -> None:
    with pytest.raises(ProvenanceError, match="missing"):
        verify_file(tmp_path / "missing.zip", expected_size=1, expected_sha256="0" * 64)

    source = tmp_path / "source.zip"
    source.write_bytes(b"changed")
    with pytest.raises(ProvenanceError, match="SHA-256"):
        verify_file(source, expected_size=7, expected_sha256="0" * 64)


def test_verify_file_rejects_md5_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"source bytes")
    sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(ProvenanceError, match="MD5"):
        verify_file(
            source,
            expected_size=12,
            expected_sha256=sha256,
            expected_md5="0" * 32,
        )

