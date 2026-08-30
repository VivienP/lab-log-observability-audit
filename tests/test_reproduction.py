from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

from lab_log_audit.reproduce import reproduce


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in sorted(members.items()):
            archive.writestr(name, content)


def _entry(path: Path, dataset: str) -> dict[str, object]:
    return {
        "dataset": dataset,
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def test_reproduction_writes_stable_machine_readable_outputs(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    flex = raw / "flex.zip"
    metadata = raw / "metadata.zip"
    logs = raw / "logs.zip"
    eventlog = "".join(
        [
            '2026-01-01 00:00:00\t0\t<start type="operation" operationid="1"><Move/></start>\n',
            '2026-01-01 00:00:01\t1\t<end type="operation" operationid="1"><Move/></end>\n',
        ]
    )
    _write_zip(flex, {"Flex/Chemspeed/run/Eventlog.txt": eventlog})
    _write_zip(
        metadata,
        {
            "metadata/Operation/mix/op/exp.yaml": """
anomalies:
  - class: ConfirmedAnomaly
    id: A1
    hasEnd: '12:20:00'
    hasRecoveryAction: restore
    PerturbationMode:
      hasEnd: '12:10:00'
"""
        },
    )
    _write_zip(logs, {"logs/mix/op/exp.csv": "Time,Property,Value\n12:10:00,Event,-\n"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [_entry(flex, "flexcat"), _entry(metadata, "batch_metadata"), _entry(logs, "batch_logs")],
                "expected_metrics": {
                    "chemspeed_total_actions": 1,
                    "chemspeed_clean_actions": 1,
                    "batch_labelled_recoveries": 1,
                    "batch_matched_recoveries_original_window": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    results = tmp_path / "results"
    derived = tmp_path / "derived"

    reproduce(manifest, raw, results, derived)
    first = {path.name: path.read_bytes() for path in results.iterdir() if path.is_file()}
    reproduce(manifest, raw, results, derived)
    second = {path.name: path.read_bytes() for path in results.iterdir() if path.is_file()}

    assert first == second
    metrics = json.loads((results / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["batch_distillation"]["original_window"]["matched_actions"] == 1
    sensitivity = (results / "window_sensitivity.csv").read_text(encoding="utf-8")
    assert "60,120,1,1,1.000000" in sensitivity
    assert "300,300,1,1,1.000000" in sensitivity
    assert "600,600,1,1,1.000000" in sensitivity
    checksums = (results / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "  metrics.json\n" in checksums
    assert "  recovery_windows.csv\n" in checksums
    assert "  window_sensitivity.csv\n" in checksums


CANONICAL_FLEXCAT_BYTES = 7_182_559
CANONICAL_FLEXCAT_MD5 = "42099be25d1e963a87d123f1cd04ad4d"
CANONICAL_FLEXCAT_SHA256 = "1611bf97f6efab2694050eb4c48e50084ef65362b797d7c480dacf1ea3fd3857"
CANONICAL_EVENTLOG_SHA256 = "dbf9a624b54ebb5a287504af4ce6cd81f4a71c784da8a2c2fdc77112026c1f3b"


def test_manifest_flexcat_pins_canonical_zenodo_zip() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8"))
    flexcat = next(entry for entry in manifest["files"] if entry["dataset"] == "flexcat")

    assert flexcat["bytes"] == CANONICAL_FLEXCAT_BYTES
    assert flexcat["md5"] == CANONICAL_FLEXCAT_MD5
    assert flexcat["sha256"] == CANONICAL_FLEXCAT_SHA256
    assert flexcat["member_sha256"] == CANONICAL_EVENTLOG_SHA256


def test_committed_results_match_manifest_invariants() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "results" / "metrics.json").read_text(encoding="utf-8"))
    expected = manifest["expected_metrics"]

    assert metrics["chemspeed"]["actions"]["total"] == expected["chemspeed_total_actions"]
    assert metrics["chemspeed"]["actions"]["clean"]["numerator"] == expected["chemspeed_clean_actions"]
    endpoints = metrics["chemspeed"]["transfer_endpoints"]
    assert endpoints["with_reported_actual_volume"] == expected[
        "chemspeed_transfer_endpoints_with_actual_volume"
    ]
    assert endpoints["skipped_incomplete_endpoints"] == expected[
        "chemspeed_skipped_incomplete_endpoints"
    ]
    assert endpoints["reported_equals_requested"]["numerator"] == expected[
        "chemspeed_transfer_endpoints_equal_requested"
    ]
    original = metrics["batch_distillation"]["original_window"]
    assert original["total_actions"] == expected["batch_labelled_recoveries"]
    assert original["matched_actions"] == expected[
        "batch_matched_recoveries_original_window"
    ]
    with (root / "data" / "derived" / "observations.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert len(list(csv.DictReader(handle))) == 60
    with (root / "results" / "recovery_windows.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        assert len(list(csv.DictReader(handle))) == 79
    for line in (root / "results" / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected_digest, filename = line.split("  ", 1)
        actual_digest = hashlib.sha256((root / "results" / filename).read_bytes()).hexdigest()
        assert actual_digest == expected_digest
