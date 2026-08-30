"""Small metric functions with explicit numerators and denominators."""

from __future__ import annotations

from collections.abc import Sequence

from .load import ChemspeedAudit
from .matching import RecoveryMatch


def _ratio(numerator: int, denominator: int) -> dict[str, float | int]:
    if denominator == 0:
        raise ValueError("metric denominator must be greater than zero")
    return {
        "numerator": numerator,
        "denominator": denominator,
        "fraction": numerator / denominator,
    }


def _optional_ratio(numerator: int, denominator: int) -> dict[str, float | int | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "fraction": numerator / denominator if denominator else None,
    }


def chemspeed_metrics(audit: ChemspeedAudit) -> dict[str, object]:
    total_actions = len(audit.actions)
    clean_actions = sum(action.pairing_status == "clean" for action in audit.actions)
    with_readback = len(audit.transfer_observations)
    equal = sum(not observation.diverges for observation in audit.transfer_observations)
    return {
        "actions": {
            "total": total_actions,
            "clean": _ratio(clean_actions, total_actions),
            "pairing_status_counts": {
                status: sum(action.pairing_status == status for action in audit.actions)
                for status in (
                    "clean",
                    "missing_start",
                    "missing_end",
                    "duplicate_start",
                    "duplicate_end",
                    "count_mismatch",
                )
            },
        },
        "transfer_endpoints": {
            "with_reported_actual_volume": with_readback,
            "skipped_incomplete_endpoints": audit.skipped_incomplete_endpoints,
            "reported_equals_requested": _optional_ratio(equal, with_readback),
            "evidence_class": "reported_value_semantics_unverified",
            "independent_physical_measurement_established": False,
        },
    }


def recovery_metrics(matches: Sequence[RecoveryMatch]) -> dict[str, float | int]:
    total = len(matches)
    if total == 0:
        raise ValueError("metric denominator must be greater than zero")
    matched = sum(bool(match.events) for match in matches)
    return {"matched_actions": matched, "total_actions": total, "coverage": matched / total}
