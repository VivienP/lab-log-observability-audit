from __future__ import annotations

import csv
import hashlib
import json
import zipfile
from pathlib import Path

from lab_log_audit.background import NULL_ITERATIONS, NULL_SEED, background_null
from lab_log_audit.load import Event, Recovery
from lab_log_audit.reproduce import (
    BACKGROUND_FIGURE_NAME,
    NULL_VARIANTS,
    WINDOWS,
    reproduce,
)


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
    # The log spans more than the widest window so the null has a usable anchor domain.
    _write_zip(
        logs,
        {
            "logs/mix/op/exp.csv": (
                "Time,Property,Value\n11:00:00,Event,-\n12:10:00,Event,-\n13:00:00,Event,-\n"
            )
        },
    )
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
    assert "  background_null.csv\n" in checksums
    assert f"  {BACKGROUND_FIGURE_NAME}\n" in checksums
    assert (results / BACKGROUND_FIGURE_NAME).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    null = metrics["batch_distillation"]["background_null"]
    assert null["seed"] == NULL_SEED
    assert null["iterations"] == NULL_ITERATIONS
    assert [entry["pre_window_seconds"] for entry in null["windows"]] == [60, 300, 600]
    with (results / "background_null.csv").open(encoding="utf-8", newline="") as handle:
        variants = list(csv.DictReader(handle))
    assert len(variants) == len(WINDOWS) * len(NULL_VARIANTS)
    assert sum(row["variant_role"] == "primary" for row in variants) == len(WINDOWS)


def test_null_analyses_the_same_recovery_set_under_every_window(tmp_path: Path) -> None:
    """The figure compares windows, so the analysed denominator must not drift."""
    events = {
        "exp": tuple(
            Event("exp", row, time, "event", "-", "other")
            for row, time in enumerate(("09:00:00", "10:00:00", "12:00:00"))
        )
    }
    recoveries = tuple(
        Recovery("exp", anomaly_id, "restore", (anchor,), (), anchor, "perturbation_end")
        for anomaly_id, anchor in (("A", "09:30:00"), ("B", "11:00:00"), ("C", "23:00:00"))
    )

    analysed = {
        (window.pre_seconds, window.post_seconds): background_null(
            recoveries, events, window, iterations=10
        ).analysed_recoveries
        for window in WINDOWS
    }

    assert set(analysed.values()) == {2}


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


def test_committed_background_null_is_internally_consistent() -> None:
    root = Path(__file__).resolve().parents[1]
    metrics = json.loads((root / "results" / "metrics.json").read_text(encoding="utf-8"))
    null = metrics["batch_distillation"]["background_null"]
    inclusion = metrics["batch_distillation"]["inclusion"]
    with (root / "results" / "background_null.csv").open(encoding="utf-8", newline="") as handle:
        variants = list(csv.DictReader(handle))

    assert null["seed"] == NULL_SEED
    assert null["iterations"] == NULL_ITERATIONS
    assert inclusion["anchored_outside_observable_log_interval"] == 5
    assert len(inclusion["anchored_outside_source_records"]) == 5

    # Counts the companion article quotes, so they stay verifiable from the repo.
    census = inclusion["anomaly_records"]
    assert inclusion["experiments_with_operation_log"] == 106
    assert census["deduplicated_total"] == 256
    assert census["with_anomaly_class"] == 237
    assert census["confirmed_anomaly"] == 137
    assert census["with_anomaly_class"] + census["without_anomaly_class"] == census[
        "deduplicated_total"
    ]
    assert sum(census["by_class"].values()) == census["deduplicated_total"]

    for entry in null["windows"]:
        # The comparison denominator is the 79 included recoveries minus the ones
        # the operation log never covers, and it must not drift between windows.
        assert entry["analysed_recoveries"] == 74
        assert entry["excluded_recoveries"] == 5
        assert sum(entry["null_matched_count_histogram"].values()) == NULL_ITERATIONS
        # The simulation must agree with the exact per-anchor probability.
        assert abs(entry["expected_fraction"] - entry["analytic_expected_fraction"]) < 0.01
        assert entry["observed_matched"] / entry["analysed_recoveries"] == entry[
            "observed_fraction"
        ]

    original = next(entry for entry in null["windows"] if entry["pre_window_seconds"] == 60)
    widest = next(entry for entry in null["windows"] if entry["pre_window_seconds"] == 600)
    # Headline claim: the original window is far above background, the widest is not.
    assert original["ratio_observed_over_expected"] > 2.0
    assert original["empirical_p_value"] < 0.01
    assert 0.9 < widest["ratio_observed_over_expected"] < 1.1
    assert widest["empirical_p_value"] > 0.05

    # Every variant must reach the same qualitative verdict for the original window.
    for row in (entry for entry in variants if entry["pre_window_seconds"] == "60"):
        assert float(row["ratio_observed_over_expected"]) > 2.0
        assert float(row["empirical_p_value"]) < 0.01


def test_committed_notebook_has_current_outputs() -> None:
    """The notebook is a view of the committed results, so a stale one is a defect."""
    root = Path(__file__).resolve().parents[1]
    notebook = json.loads((root / "notebooks" / "audit.ipynb").read_text(encoding="utf-8"))
    metrics = json.loads((root / "results" / "metrics.json").read_text(encoding="utf-8"))
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    rendered = "".join(
        "".join(output.get("text", ""))
        for cell in code_cells
        for output in cell["outputs"]
    )

    assert code_cells, "notebook has no code cells"
    assert all(cell["outputs"] for cell in code_cells), "a code cell has no committed output"
    assert all(cell["id"] == f"cell-{index:02d}" for index, cell in enumerate(notebook["cells"]))
    # No committed artefact carries a run timestamp, the notebook included.
    assert not any("execution" in cell.get("metadata", {}) for cell in notebook["cells"])
    assert any(
        output.get("data", {}).get("image/png")
        for cell in code_cells
        for output in cell["outputs"]
    ), "the generated figure is not displayed"

    original = metrics["batch_distillation"]["original_window"]
    null = metrics["batch_distillation"]["background_null"]["windows"][0]
    assert f"{original['matched_actions']} / {original['total_actions']}" in rendered
    assert f"{null['observed_matched']}/{null['analysed_recoveries']}" in rendered
    assert f"{null['ratio_observed_over_expected']:.2f}x" in rendered
