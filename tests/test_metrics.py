from __future__ import annotations

import pytest

from lab_log_audit.load import Action, ChemspeedAudit, Recovery, TransferObservation
from lab_log_audit.matching import RecoveryMatch, Window
from lab_log_audit.metrics import chemspeed_metrics, recovery_metrics


def test_chemspeed_metrics_expose_numerators_and_denominators() -> None:
    audit = ChemspeedAudit(
        actions=(
            Action(1, "1", "clean", 1, 1, "source"),
            Action(1, "2", "missing_end", 1, 0, "source"),
        ),
        transfer_observations=(
            TransferObservation("T", "src", 0, "0", "W", "1.0", "1.0", "0.0", False, "source"),
        ),
    )

    result = chemspeed_metrics(audit)

    assert result["actions"]["clean"]["numerator"] == 1
    assert result["actions"]["clean"]["denominator"] == 2
    assert result["transfer_endpoints"]["reported_equals_requested"]["numerator"] == 1
    assert result["transfer_endpoints"]["skipped_incomplete_endpoints"] == 0
    assert result["transfer_endpoints"]["evidence_class"] == "reported_value_semantics_unverified"


def test_recovery_metrics_compute_coverage_from_matches() -> None:
    recovery = Recovery("exp", "A", "restore", ("12:00:00",), (), "12:00:00", "perturbation_end")
    matches = (
        RecoveryMatch(recovery, (), Window(60, 120)),
        RecoveryMatch(recovery, (), Window(60, 120)),
        RecoveryMatch(recovery, (object(),), Window(60, 120)),  # type: ignore[arg-type]
    )

    result = recovery_metrics(matches)

    assert result == {
        "matched_actions": 1,
        "total_actions": 3,
        "coverage": pytest.approx(1 / 3),
    }


def test_recovery_metrics_reject_empty_denominator() -> None:
    with pytest.raises(ValueError, match="denominator"):
        recovery_metrics(())
