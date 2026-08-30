"""Deterministic orchestration for the complete public audit."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .load import ChemspeedAudit, load_batch_distillation, load_chemspeed_archive
from .matching import RecoveryMatch, Window, match_all, parse_time_of_day
from .metrics import chemspeed_metrics, recovery_metrics
from .provenance import sha256_file, verify_file

WINDOWS = (Window(60, 120), Window(300, 300), Window(600, 600))


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read manifest {path}: {exc}") from exc
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("manifest must have schema_version=1 and a files list")
    return manifest


def _input_paths(manifest: dict[str, Any], raw_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for entry in manifest["files"]:
        path = raw_dir / entry["filename"]
        verify_file(
            path,
            expected_size=int(entry["bytes"]),
            expected_sha256=str(entry["sha256"]),
            expected_md5=str(entry["md5"]) if entry.get("md5") else None,
        )
        paths[str(entry["dataset"])] = path
    required = {"flexcat", "batch_metadata", "batch_logs"}
    if set(paths) != required:
        raise ValueError(f"manifest dataset keys must be exactly {sorted(required)}")
    return paths


def _flat_invariants(
    chemspeed: dict[str, object], original: dict[str, float | int]
) -> dict[str, int]:
    actions = chemspeed["actions"]
    endpoints = chemspeed["transfer_endpoints"]
    assert isinstance(actions, dict) and isinstance(endpoints, dict)
    clean = actions["clean"]
    equal = endpoints["reported_equals_requested"]
    assert isinstance(clean, dict)
    assert isinstance(equal, dict)
    return {
        "chemspeed_total_actions": int(actions["total"]),
        "chemspeed_clean_actions": int(clean["numerator"]),
        "chemspeed_transfer_endpoints_with_actual_volume": int(
            endpoints["with_reported_actual_volume"]
        ),
        "chemspeed_skipped_incomplete_endpoints": int(endpoints["skipped_incomplete_endpoints"]),
        "chemspeed_transfer_endpoints_equal_requested": int(equal["numerator"]),
        "batch_labelled_recoveries": int(original["total_actions"]),
        "batch_matched_recoveries_original_window": int(original["matched_actions"]),
    }


def _check_invariants(expected: dict[str, Any], actual: dict[str, int]) -> None:
    problems = [
        f"{name}: expected {value}, got {actual.get(name)}"
        for name, value in expected.items()
        if actual.get(name) != value
    ]
    if problems:
        raise RuntimeError("expected result invariant(s) failed: " + "; ".join(problems))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _observation_id(*parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return "obs-" + hashlib.sha256(material).hexdigest()[:16]


def _write_result_checksums(results_dir: Path) -> None:
    names = ("metrics.json", "recovery_windows.csv", "window_sensitivity.csv")
    lines = [f"{sha256_file(results_dir / name)}  {name}" for name in names]
    (results_dir / "SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )


def _write_transfer_observations(path: Path, audit: ChemspeedAudit) -> None:
    rows: list[dict[str, object]] = []
    for item in audit.transfer_observations:
        classification = (
            "reported_differs_from_requested" if item.diverges else "reported_equals_requested"
        )
        rows.append(
            {
                "observation_id": _observation_id(
                    item.source_reference,
                    item.transfer_id,
                    item.endpoint_kind,
                    item.endpoint_index,
                ),
                "action_id": item.transfer_id,
                "action_timestamp": item.action_timestamp,
                "action_type": "liquid_transfer",
                "candidate_evidence_timestamp": item.evidence_timestamp,
                "candidate_evidence_type": "reported_actualVolume_semantics_unverified",
                "classification": classification,
                "reason": (
                    f"reported actualVolume={item.reported_actual_volume}; "
                    f"requested volume={item.requested_volume}; exact decimal delta={item.delta}"
                ),
                "source_reference": (
                    f"{item.source_reference}#transfer={item.transfer_id};"
                    f"{item.endpoint_kind}[{item.endpoint_index}]"
                ),
                "review_method": "automated_exact_decimal_comparison",
            }
        )
    _write_csv(
        path,
        [
            "observation_id",
            "action_id",
            "action_timestamp",
            "action_type",
            "candidate_evidence_timestamp",
            "candidate_evidence_type",
            "classification",
            "reason",
            "source_reference",
            "review_method",
        ],
        rows,
    )


def _write_recovery_windows(path: Path, matches: tuple[RecoveryMatch, ...]) -> None:
    rows: list[dict[str, object]] = []
    for match in matches:
        rows.append(
            {
                "observation_id": _observation_id(
                    match.recovery.experiment, match.recovery.anomaly_id, 60, 120
                ),
                "action_id": match.recovery.anomaly_id,
                "action_timestamp": match.recovery.anchor_time,
                "action_type": "labelled_recovery",
                "candidate_evidence_timestamp": ";".join(event.time_raw for event in match.events),
                "candidate_evidence_type": "operation_log_event",
                "classification": "matched" if match.events else "no_logged_event_in_window",
                "reason": f"{len(match.events)} event(s) in inclusive temporal window",
                "source_reference": match.recovery.experiment,
                "anchor_source": match.recovery.anchor_source,
                "recovery_action": match.recovery.recovery_action,
                "pre_window_seconds": match.window.pre_seconds,
                "post_window_seconds": match.window.post_seconds,
                "matched_event_count": len(match.events),
                "matched_event_rows": ";".join(str(event.row_index) for event in match.events),
                "review_method": "automated_temporal_match",
            }
        )
    _write_csv(
        path,
        [
            "observation_id",
            "action_id",
            "action_timestamp",
            "action_type",
            "candidate_evidence_timestamp",
            "candidate_evidence_type",
            "classification",
            "reason",
            "source_reference",
            "anchor_source",
            "recovery_action",
            "pre_window_seconds",
            "post_window_seconds",
            "matched_event_count",
            "matched_event_rows",
            "review_method",
        ],
        rows,
    )


def reproduce(manifest_path: Path, raw_dir: Path, results_dir: Path, derived_dir: Path) -> dict[str, object]:
    manifest = _load_manifest(manifest_path)
    paths = _input_paths(manifest, raw_dir)
    chemspeed_audit = load_chemspeed_archive(paths["flexcat"])
    recoveries, events, excluded_recoveries = load_batch_distillation(
        paths["batch_metadata"], paths["batch_logs"]
    )

    matches_by_window = {
        (window.pre_seconds, window.post_seconds): match_all(recoveries, events, window)
        for window in WINDOWS
    }
    original_matches = matches_by_window[(60, 120)]
    chemspeed = chemspeed_metrics(chemspeed_audit)
    original = recovery_metrics(original_matches)
    actual_invariants = _flat_invariants(chemspeed, original)
    _check_invariants(manifest.get("expected_metrics", {}), actual_invariants)

    metrics: dict[str, object] = {
        "schema_version": 1,
        "research_question": (
            "To what extent can software-level laboratory actions be traced to independent "
            "evidence of their physical execution?"
        ),
        "chemspeed": chemspeed,
        "batch_distillation": {
            "inclusion": {
                "metadata_labelled_recoveries": len(recoveries) + len(excluded_recoveries),
                "included_with_operation_log": len(recoveries),
                "excluded_without_operation_log": len(excluded_recoveries),
                "excluded_source_records": [
                    f"{item.experiment}::{item.anomaly_id}" for item in excluded_recoveries
                ],
                "unparseable_event_timestamps": sum(
                    1
                    for rows in events.values()
                    for event in rows
                    if parse_time_of_day(event.time_raw) is None
                ),
            },
            "original_window": {
                "pre_window_seconds": 60,
                "post_window_seconds": 120,
                **original,
            },
            "coverage_definition": (
                "labelled recovery with at least one operation-log row whose parseable "
                "time of day lies in the inclusive window; observability/activity proxy, "
                "not evidence that the labelled recovery action itself was observed"
            ),
        },
    }
    _write_json(results_dir / "metrics.json", metrics)

    sensitivity_rows: list[dict[str, object]] = []
    for window in WINDOWS:
        values = recovery_metrics(matches_by_window[(window.pre_seconds, window.post_seconds)])
        sensitivity_rows.append(
            {
                "pre_window_seconds": window.pre_seconds,
                "post_window_seconds": window.post_seconds,
                "matched_actions": values["matched_actions"],
                "total_actions": values["total_actions"],
                "coverage": f"{float(values['coverage']):.6f}",
            }
        )
    _write_csv(
        results_dir / "window_sensitivity.csv",
        [
            "pre_window_seconds",
            "post_window_seconds",
            "matched_actions",
            "total_actions",
            "coverage",
        ],
        sensitivity_rows,
    )
    _write_recovery_windows(results_dir / "recovery_windows.csv", original_matches)
    _write_transfer_observations(derived_dir / "observations.csv", chemspeed_audit)
    _write_result_checksums(results_dir)
    return metrics
