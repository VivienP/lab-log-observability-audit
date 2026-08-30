"""Deterministic orchestration for the complete public audit."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .background import (
    NULL_ITERATIONS,
    NULL_SEED,
    NullSummary,
    anchors_outside_observable_interval,
    background_null,
    event_seconds,
    observable_interval,
)
from .figures import render_background_figure
from .load import (
    ChemspeedAudit,
    Event,
    Recovery,
    anomaly_census,
    load_batch_distillation,
    load_chemspeed_archive,
)
from .matching import RecoveryMatch, Window, match_all, parse_time_of_day
from .metrics import chemspeed_metrics, recovery_metrics
from .provenance import sha256_file, verify_file

WINDOWS = (Window(60, 120), Window(300, 300), Window(600, 600))

PRIMARY_NULL_VARIANT = ("interior", "independent")
NULL_VARIANTS = (
    PRIMARY_NULL_VARIANT,
    ("interior", "experiment_shift"),
    ("full_interval", "independent"),
    ("full_interval", "experiment_shift"),
)
BACKGROUND_FIGURE_NAME = "figures/recovery_activity_vs_background.png"


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
    names = (
        BACKGROUND_FIGURE_NAME,
        "background_null.csv",
        "metrics.json",
        "recovery_windows.csv",
        "window_sensitivity.csv",
    )
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


def _write_recovery_windows(
    path: Path,
    matches: tuple[RecoveryMatch, ...],
    events: dict[str, tuple[Event, ...]],
) -> None:
    rows: list[dict[str, object]] = []
    for match in matches:
        interval = observable_interval(event_seconds(events[match.recovery.experiment]))
        anchor = parse_time_of_day(match.recovery.anchor_time)
        within = (
            interval is not None
            and anchor is not None
            and interval[0] <= anchor <= interval[1]
        )
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
                "anchor_within_observable_log_interval": str(within).lower(),
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
            "anchor_within_observable_log_interval",
            "pre_window_seconds",
            "post_window_seconds",
            "matched_event_count",
            "matched_event_rows",
            "review_method",
        ],
        rows,
    )


def _run_null_variants(
    recoveries: tuple[Recovery, ...],
    events: dict[str, tuple[Event, ...]],
) -> dict[tuple[int, int, str, str], NullSummary]:
    return {
        (window.pre_seconds, window.post_seconds, domain_mode, resampling): background_null(
            recoveries,
            events,
            window,
            anchor_domain_mode=domain_mode,
            resampling=resampling,
        )
        for window in WINDOWS
        for domain_mode, resampling in NULL_VARIANTS
    }


def _null_row(summary: NullSummary, *, is_primary: bool) -> dict[str, object]:
    ratio = summary.ratio_observed_over_expected
    return {
        "pre_window_seconds": summary.pre_window_seconds,
        "post_window_seconds": summary.post_window_seconds,
        "anchor_domain_mode": summary.anchor_domain_mode,
        "resampling": summary.resampling,
        "variant_role": "primary" if is_primary else "sensitivity",
        "seed": summary.seed,
        "iterations": summary.iterations,
        "analysed_recoveries": summary.analysed_recoveries,
        "excluded_recoveries": summary.excluded_recoveries,
        "observed_matched": summary.observed_matched,
        "observed_fraction": f"{summary.observed_fraction:.6f}",
        "expected_fraction": f"{summary.expected_fraction:.6f}",
        "analytic_expected_fraction": f"{summary.analytic_expected_fraction:.6f}",
        "ratio_observed_over_expected": "" if ratio is None else f"{ratio:.6f}",
        "null_p05": f"{summary.null_percentiles['p05']:.6f}",
        "null_p50": f"{summary.null_percentiles['p50']:.6f}",
        "null_p95": f"{summary.null_percentiles['p95']:.6f}",
        "null_stdev": f"{summary.null_stdev:.6f}",
        "observed_percentile_in_null": f"{summary.observed_percentile_in_null:.6f}",
        "empirical_p_value": f"{summary.empirical_p_value:.6f}",
    }


def _null_metrics(summary: NullSummary) -> dict[str, object]:
    ratio = summary.ratio_observed_over_expected
    return {
        "pre_window_seconds": summary.pre_window_seconds,
        "post_window_seconds": summary.post_window_seconds,
        "analysed_recoveries": summary.analysed_recoveries,
        "excluded_recoveries": summary.excluded_recoveries,
        "exclusion_reasons": summary.exclusion_reasons,
        "observed_matched": summary.observed_matched,
        "observed_fraction": summary.observed_fraction,
        "expected_fraction": summary.expected_fraction,
        "analytic_expected_fraction": summary.analytic_expected_fraction,
        "ratio_observed_over_expected": ratio,
        "null_percentiles": summary.null_percentiles,
        "null_min": summary.null_min,
        "null_max": summary.null_max,
        "null_stdev": summary.null_stdev,
        "observed_percentile_in_null": summary.observed_percentile_in_null,
        "empirical_p_value": summary.empirical_p_value,
        "null_matched_count_histogram": {
            str(count): occurrences
            for count, occurrences in summary.null_matched_count_histogram.items()
        },
    }


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
    nulls = _run_null_variants(recoveries, events)
    outside_interval = anchors_outside_observable_interval(recoveries, events)
    census = anomaly_census(paths["batch_metadata"])

    metrics: dict[str, object] = {
        "schema_version": 2,
        "research_question": (
            "To what extent can software-level laboratory actions be traced to independent "
            "evidence of their physical execution?"
        ),
        "chemspeed": chemspeed,
        "batch_distillation": {
            "inclusion": {
                "experiments_with_operation_log": len(events),
                "anomaly_records": census,
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
                "anchored_outside_observable_log_interval": len(outside_interval),
                "anchored_outside_source_records": list(outside_interval),
                "anchored_outside_note": (
                    "the operation log for these experiments does not cover the labelled "
                    "anchor instant at all, so no window of any width can match them; they "
                    "are unanswerable for the coverage question rather than negative answers "
                    "to it, and they are excluded from the background comparison"
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
            "background_null": {
                "method": (
                    "the labelled anchor is replaced by a uniform random anchor drawn "
                    "inside the same experiment's own observable operation-log interval, "
                    "keeping the window and that experiment's event structure fixed; "
                    "timestamps are never pooled across experiments"
                ),
                "interpretation": (
                    "a ratio above one means operation-log rows cluster near recovery "
                    "labels more than near arbitrary instants of the same log; it remains "
                    "temporal association and is not causal evidence"
                ),
                "seed": NULL_SEED,
                "iterations": NULL_ITERATIONS,
                "primary_variant": {
                    "anchor_domain_mode": PRIMARY_NULL_VARIANT[0],
                    "resampling": PRIMARY_NULL_VARIANT[1],
                    "anchor_domain_rule": (
                        "uniform over the instants whose full window fits inside the "
                        "observable interval; all 74 comparable real anchors satisfy that "
                        "condition, and the 5 excluded recoveries are anchored outside "
                        "their experiment's observable interval, so no window can match them"
                    ),
                },
                "sensitivity_variants": (
                    "results/background_null.csv holds the interior and full_interval "
                    "anchor domains crossed with independent and experiment_shift "
                    "resampling; experiment_shift rotates all anchors of one experiment "
                    "by a shared offset and so preserves repeated recoveries"
                ),
                "windows": [
                    _null_metrics(nulls[(window.pre_seconds, window.post_seconds, *PRIMARY_NULL_VARIANT)])
                    for window in WINDOWS
                ],
            },
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
    _write_csv(
        results_dir / "background_null.csv",
        [
            "pre_window_seconds",
            "post_window_seconds",
            "anchor_domain_mode",
            "resampling",
            "variant_role",
            "seed",
            "iterations",
            "analysed_recoveries",
            "excluded_recoveries",
            "observed_matched",
            "observed_fraction",
            "expected_fraction",
            "analytic_expected_fraction",
            "ratio_observed_over_expected",
            "null_p05",
            "null_p50",
            "null_p95",
            "null_stdev",
            "observed_percentile_in_null",
            "empirical_p_value",
        ],
        [
            _null_row(
                nulls[(window.pre_seconds, window.post_seconds, domain_mode, resampling)],
                is_primary=(domain_mode, resampling) == PRIMARY_NULL_VARIANT,
            )
            for window in WINDOWS
            for domain_mode, resampling in NULL_VARIANTS
        ],
    )
    render_background_figure(
        results_dir / BACKGROUND_FIGURE_NAME,
        [
            nulls[(window.pre_seconds, window.post_seconds, *PRIMARY_NULL_VARIANT)]
            for window in WINDOWS
        ],
    )
    _write_recovery_windows(results_dir / "recovery_windows.csv", original_matches, events)
    _write_transfer_observations(derived_dir / "observations.csv", chemspeed_audit)
    _write_result_checksums(results_dir)
    return metrics
