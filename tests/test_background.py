from __future__ import annotations

import pytest

from lab_log_audit.background import (
    NULL_ITERATIONS,
    NULL_SEED,
    analytic_match_probability,
    anchor_domain,
    anchor_matches,
    anchors_outside_observable_interval,
    background_null,
    event_seconds,
    observable_interval,
)
from lab_log_audit.load import Event, Recovery
from lab_log_audit.matching import Window

WINDOW = Window(60, 120)


def _event(time: str, row: int = 0, experiment: str = "exp") -> Event:
    return Event(
        experiment=experiment,
        row_index=row,
        time_raw=time,
        property="event",
        value="-",
        event_category="other",
    )


def _recovery(anchor: str, anomaly_id: str = "A1", experiment: str = "exp") -> Recovery:
    return Recovery(
        experiment=experiment,
        anomaly_id=anomaly_id,
        recovery_action="Restore normal state",
        perturbation_end=(anchor,),
        anomaly_end=(),
        anchor_time=anchor,
        anchor_source="perturbation_end",
    )


def _log(*times: str, experiment: str = "exp") -> tuple[Event, ...]:
    return tuple(_event(time, row, experiment) for row, time in enumerate(times))


def test_event_seconds_sorts_and_drops_unparseable_timestamps() -> None:
    events = _log("12:00:10", "", "11:00:00", "12:10", "10:00:00")

    assert event_seconds(events) == (36_000, 39_600, 43_210)


def test_observable_interval_is_first_and_last_parseable_instant() -> None:
    assert observable_interval(event_seconds(_log("12:00:00", "10:00:00"))) == (36_000, 43_200)
    assert observable_interval(event_seconds(_log("12:10", ""))) is None


def test_interior_anchor_domain_guarantees_the_whole_window_fits() -> None:
    interval = (1_000, 2_000)

    assert anchor_domain(interval, WINDOW, "interior") == (1_060, 1_880)
    assert anchor_domain(interval, WINDOW, "full_interval") == (1_000, 2_000)


def test_interior_anchor_domain_is_empty_when_the_interval_is_shorter_than_the_window() -> None:
    assert anchor_domain((1_000, 1_100), WINDOW, "interior") is None
    assert anchor_domain((1_000, 1_000), WINDOW, "interior") is None
    assert anchor_domain((1_000, 1_180), WINDOW, "interior") == (1_060, 1_060)
    assert anchor_domain(None, WINDOW, "interior") is None


def test_anchor_domain_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="anchor domain mode"):
        anchor_domain((0, 10), WINDOW, "whatever")


def test_anchor_matching_boundaries_are_inclusive_and_asymmetric() -> None:
    times = (1_000,)

    assert anchor_matches(times, 1_060, WINDOW) is True
    assert anchor_matches(times, 1_061, WINDOW) is False
    assert anchor_matches(times, 880, WINDOW) is True
    assert anchor_matches(times, 879, WINDOW) is False


def test_analytic_probability_matches_a_hand_computed_union() -> None:
    # Two events 100 s apart: their [t-120, t+60] anchor sets overlap and merge
    # into one run of 100 + 181 = 281 instants inside a 1001-instant domain.
    assert analytic_match_probability((1_000, 1_100), (500, 1_500), WINDOW) == pytest.approx(
        281 / 1001
    )
    # Two events 1000 s apart: two disjoint runs of 181 instants each.
    assert analytic_match_probability((1_000, 2_000), (0, 9_999), WINDOW) == pytest.approx(
        362 / 10_000
    )


def test_analytic_probability_clips_runs_to_the_anchor_domain() -> None:
    assert analytic_match_probability((1_000,), (1_000, 1_000), WINDOW) == 1.0
    assert analytic_match_probability((1_000,), (2_000, 2_100), WINDOW) == 0.0


def test_analytic_probability_rejects_an_empty_domain() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        analytic_match_probability((1_000,), (2_000, 1_000), WINDOW)


def test_null_is_reproducible_for_a_fixed_seed_and_varies_with_the_seed() -> None:
    events = {"exp": _log("10:00:00", "10:20:00", "10:40:00", "11:00:00")}
    recoveries = (_recovery("10:20:00"),)

    first = background_null(recoveries, events, WINDOW, iterations=500)
    second = background_null(recoveries, events, WINDOW, iterations=500)
    other = background_null(recoveries, events, WINDOW, iterations=500, seed=NULL_SEED + 1)

    assert first == second
    assert first.seed == NULL_SEED
    assert first.null_matched_count_histogram != other.null_matched_count_histogram


def test_simulated_expectation_converges_on_the_exact_analytic_probability() -> None:
    events = {"exp": _log("10:00:00", "10:05:00", "10:30:00", "11:00:00", "11:30:00")}
    recoveries = (_recovery("10:30:00"),)

    summary = background_null(recoveries, events, WINDOW, iterations=NULL_ITERATIONS)

    assert summary.expected_fraction == pytest.approx(
        summary.analytic_expected_fraction, abs=0.02
    )
    assert 0.0 < summary.analytic_expected_fraction < 1.0


def test_sampled_anchors_never_leave_the_declared_domain() -> None:
    # A dense 60 s grid across a narrow interval: any in-domain anchor matches,
    # so a single out-of-domain draw would show up as a non-unit fraction.
    times = [f"10:{minute:02d}:00" for minute in range(0, 60, 1)]
    events = {"exp": _log(*times)}
    recoveries = (_recovery("10:30:00"),)

    summary = background_null(recoveries, events, WINDOW, iterations=2_000)

    assert summary.null_min == 1.0
    assert summary.null_max == 1.0
    assert summary.analytic_expected_fraction == 1.0


def test_experiment_with_no_parseable_event_timestamp_is_excluded() -> None:
    events = {"exp": _log("not-a-time", ""), "other": _log("09:00:00", "12:00:00")}
    recoveries = (
        _recovery("10:00:00", anomaly_id="EMPTY"),
        _recovery("10:00:00", anomaly_id="DENSE", experiment="other"),
    )

    summary = background_null(recoveries, events, WINDOW)

    assert summary.analysed_recoveries == 1
    assert summary.excluded_recoveries == 1
    assert summary.exclusion_reasons == {"empty_anchor_domain": 1}


def test_dense_experiment_drives_the_background_towards_one() -> None:
    dense = _log(*[f"10:{minute:02d}:00" for minute in range(60)], experiment="dense")
    sparse = _log("08:00:00", "16:00:00", experiment="sparse")
    events = {"dense": dense, "sparse": sparse}
    recoveries = (
        _recovery("10:30:00", anomaly_id="D", experiment="dense"),
        _recovery("12:00:00", anomaly_id="S", experiment="sparse"),
    )

    summary = background_null(recoveries, events, WINDOW, iterations=2_000)

    # The dense experiment contributes a probability of 1 and the sparse one a
    # near-zero probability, so the mean sits just above one half.
    assert 0.5 <= summary.expected_fraction < 0.53
    assert summary.observed_matched == 1
    assert summary.observed_fraction == 0.5


def test_anchor_outside_the_observable_interval_is_excluded_and_reported() -> None:
    events = {"exp": _log("09:00:00", "10:00:00")}
    recoveries = (_recovery("09:30:00", anomaly_id="INSIDE"), _recovery("23:00:00", anomaly_id="OUT"))

    summary = background_null(recoveries, events, WINDOW)

    assert summary.analysed_recoveries == 1
    assert summary.exclusion_reasons == {"observed_anchor_outside_anchor_domain": 1}
    assert anchors_outside_observable_interval(recoveries, events) == ("exp::OUT",)


def test_repeated_recoveries_in_one_experiment_are_each_counted() -> None:
    events = {"exp": _log("10:00:00", "10:30:00", "11:00:00")}
    identical = tuple(_recovery("10:30:00", anomaly_id=f"A{index}") for index in range(4))

    summary = background_null(identical, events, WINDOW, iterations=1_000)

    assert summary.analysed_recoveries == 4
    assert summary.observed_matched == 4
    assert summary.observed_fraction == 1.0
    assert set(summary.null_matched_count_histogram) <= {0, 1, 2, 3, 4}
    assert sum(summary.null_matched_count_histogram.values()) == 1_000


def test_experiment_shift_moves_repeated_recoveries_together() -> None:
    events = {"exp": _log("10:00:00", "10:30:00", "11:00:00")}
    identical = tuple(_recovery("10:30:00", anomaly_id=f"A{index}") for index in range(4))

    shifted = background_null(identical, events, WINDOW, resampling="experiment_shift")
    independent = background_null(identical, events, WINDOW, resampling="independent")

    # Identical anchors rotated by one shared offset stay identical, so every
    # iteration is all-or-nothing. Independent draws also produce mixed counts.
    assert set(shifted.null_matched_count_histogram) == {0, 4}
    assert set(independent.null_matched_count_histogram) - {0, 4}
    assert shifted.expected_fraction == pytest.approx(
        independent.expected_fraction, abs=0.05
    )


def test_experiment_shift_preserves_the_spacing_of_distinct_anchors() -> None:
    # Events only at the two ends: a rigid rotation can never put both anchors,
    # which are 30 min apart, on the two events that are 60 min apart.
    events = {"exp": _log("10:00:00", "11:00:00")}
    spaced = (
        _recovery("10:15:00", anomaly_id="A"),
        _recovery("10:45:00", anomaly_id="B"),
    )

    shifted = background_null(spaced, events, WINDOW, resampling="experiment_shift")

    assert set(shifted.null_matched_count_histogram) <= {0, 1}


def test_empirical_p_value_and_percentile_bracket_the_observed_value() -> None:
    events = {"exp": _log("10:00:00", "10:30:00", "11:00:00")}
    recoveries = (_recovery("10:30:00"),)

    summary = background_null(recoveries, events, WINDOW, iterations=1_000)

    at_least = summary.null_matched_count_histogram.get(1, 0)
    below = summary.null_matched_count_histogram.get(0, 0)

    assert summary.observed_fraction == 1.0
    assert summary.empirical_p_value == pytest.approx((at_least + 1) / 1_001)
    assert summary.observed_percentile_in_null == pytest.approx(below / 1_000)
    assert summary.null_min <= summary.expected_fraction <= summary.null_max


def test_null_rejects_unusable_configuration() -> None:
    events = {"exp": _log("10:00:00", "11:00:00")}
    recoveries = (_recovery("10:30:00"),)

    with pytest.raises(ValueError, match="resampling mode"):
        background_null(recoveries, events, WINDOW, resampling="bootstrap")
    with pytest.raises(ValueError, match="iterations"):
        background_null(recoveries, events, WINDOW, iterations=0)
    with pytest.raises(ValueError, match="no recovery survives"):
        background_null((_recovery("23:00:00"),), events, WINDOW)
