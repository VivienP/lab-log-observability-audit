"""Random-anchor background comparison for recovery-window coverage.

The headline coverage metric answers only "is there at least one parseable
operation-log event near a labelled recovery?". It cannot say whether that
activity is unusual. This module answers the separate question by keeping the
window, the experiment, and the experiment's own event structure fixed while
replacing the labelled anchor with a random one drawn from the same log.

Nothing here upgrades temporal association to causal evidence. A ratio above
one means only that log rows cluster near recovery labels more than near
arbitrary instants of the same log.
"""

from __future__ import annotations

import bisect
import random
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .load import Event, Recovery
from .matching import Window, parse_time_of_day

NULL_SEED = 20260830
"""Fixed seed for :class:`random.Random` (stdlib Mersenne Twister)."""

NULL_ITERATIONS = 10_000
"""Iterations per null variant. Large enough to stabilise the 5th/95th percentiles."""

ANCHOR_DOMAIN_MODES = ("interior", "full_interval")
RESAMPLING_MODES = ("independent", "experiment_shift")

REPORTED_PERCENTILES = (1, 5, 25, 50, 75, 95, 99)


@dataclass(frozen=True, slots=True)
class NullSummary:
    """Observed coverage next to its random-anchor background, for one variant."""

    pre_window_seconds: int
    post_window_seconds: int
    anchor_domain_mode: str
    resampling: str
    seed: int
    iterations: int
    analysed_recoveries: int
    excluded_recoveries: int
    exclusion_reasons: dict[str, int]
    observed_matched: int
    observed_fraction: float
    expected_fraction: float
    analytic_expected_fraction: float
    ratio_observed_over_expected: float | None
    null_percentiles: dict[str, float]
    null_min: float
    null_max: float
    null_stdev: float
    observed_percentile_in_null: float
    empirical_p_value: float
    null_matched_count_histogram: dict[int, int]


def event_seconds(events: Sequence[Event]) -> tuple[int, ...]:
    """Sorted seconds-of-day of the events whose timestamp parses as HH:MM:SS."""
    return tuple(
        sorted(
            second
            for second in (parse_time_of_day(event.time_raw) for event in events)
            if second is not None
        )
    )


def observable_interval(times: Sequence[int]) -> tuple[int, int] | None:
    """First and last parseable instant of one experiment's operation log."""
    if not times:
        return None
    return times[0], times[-1]


def anchor_domain(
    interval: tuple[int, int] | None, window: Window, mode: str
) -> tuple[int, int] | None:
    """Inclusive integer range a random anchor may take.

    ``interior`` requires the whole window to lie inside the observable interval,
    which is the condition every real anchor in this dataset satisfies. It is
    therefore the like-for-like domain. ``full_interval`` drops that requirement
    and lets a window run past the edge of the log, matching the weaker condition
    that the real analysis merely tolerates.
    """
    if mode not in ANCHOR_DOMAIN_MODES:
        raise ValueError(f"unknown anchor domain mode: {mode!r}")
    if interval is None:
        return None
    low, high = interval
    if mode == "interior":
        low, high = low + window.pre_seconds, high - window.post_seconds
    return (low, high) if low <= high else None


def anchors_outside_observable_interval(
    recoveries: Sequence[Recovery], events_by_experiment: dict[str, tuple[Event, ...]]
) -> tuple[str, ...]:
    """Recoveries whose anchor falls outside their own experiment's logged interval.

    No temporal window can match these, at any width, because the operation log
    does not cover that instant at all. They are unanswerable for the coverage
    question rather than negative answers to it.
    """
    outside: list[str] = []
    for recovery in recoveries:
        interval = observable_interval(event_seconds(events_by_experiment[recovery.experiment]))
        anchor = parse_time_of_day(recovery.anchor_time)
        if anchor is None:
            raise ValueError(f"unparseable recovery anchor: {recovery.anchor_time!r}")
        if interval is None or not interval[0] <= anchor <= interval[1]:
            outside.append(f"{recovery.experiment}::{recovery.anomaly_id}")
    return tuple(outside)


def anchor_matches(times: Sequence[int], anchor: int, window: Window) -> bool:
    """True when at least one event lies in the inclusive window around ``anchor``."""
    index = bisect.bisect_left(times, anchor - window.pre_seconds)
    return index < len(times) and times[index] <= anchor + window.post_seconds


def analytic_match_probability(
    times: Sequence[int], domain: tuple[int, int], window: Window
) -> float:
    """Exact probability that a uniform anchor in ``domain`` matches.

    An anchor ``a`` matches event ``t`` exactly when ``t - post <= a <= t + pre``,
    so the answer is the size of the union of those intervals clipped to the
    domain, divided by the domain size. Used to cross-check the simulation.
    """
    low, high = domain
    if high < low:
        raise ValueError("anchor domain must be non-empty")
    covered = 0
    run_start: int | None = None
    run_end = 0
    for time in times:
        start = max(time - window.post_seconds, low)
        end = min(time + window.pre_seconds, high)
        if end < start:
            continue
        if run_start is None:
            run_start, run_end = start, end
        elif start <= run_end + 1:
            run_end = max(run_end, end)
        else:
            covered += run_end - run_start + 1
            run_start, run_end = start, end
    if run_start is not None:
        covered += run_end - run_start + 1
    return covered / (high - low + 1)


def _percentile(sorted_values: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile, so every reported value is an attained one."""
    rank = max(1, min(len(sorted_values), round(percent / 100 * len(sorted_values))))
    return sorted_values[rank - 1]


def background_null(
    recoveries: Sequence[Recovery],
    events_by_experiment: dict[str, tuple[Event, ...]],
    window: Window,
    *,
    anchor_domain_mode: str = "interior",
    resampling: str = "independent",
    seed: int = NULL_SEED,
    iterations: int = NULL_ITERATIONS,
) -> NullSummary:
    """Compare observed window coverage with coverage under random anchors.

    Anchors are redrawn inside the recovery's own experiment, so experiment-level
    event density is preserved and never pooled across experiments. ``independent``
    redraws every recovery separately; ``experiment_shift`` draws one offset per
    experiment and rotates all of that experiment's anchors by it, which preserves
    the spacing of repeated recoveries within a single experiment.
    """
    if resampling not in RESAMPLING_MODES:
        raise ValueError(f"unknown resampling mode: {resampling!r}")
    if iterations < 1:
        raise ValueError("iterations must be positive")

    exclusions: Counter[str] = Counter()
    analysed: list[tuple[str, int, int, int, bool]] = []
    for recovery in recoveries:
        times = event_seconds(events_by_experiment[recovery.experiment])
        anchor = parse_time_of_day(recovery.anchor_time)
        if anchor is None:
            raise ValueError(f"unparseable recovery anchor: {recovery.anchor_time!r}")
        domain = anchor_domain(observable_interval(times), window, anchor_domain_mode)
        if domain is None:
            exclusions["empty_anchor_domain"] += 1
            continue
        if not domain[0] <= anchor <= domain[1]:
            exclusions["observed_anchor_outside_anchor_domain"] += 1
            continue
        analysed.append(
            (
                recovery.experiment,
                domain[0],
                domain[1],
                anchor,
                anchor_matches(times, anchor, window),
            )
        )
    if not analysed:
        raise ValueError("no recovery survives the null-comparison inclusion rules")

    times_cache = {
        experiment: event_seconds(events_by_experiment[experiment])
        for experiment, *_ in analysed
    }
    grouped: dict[tuple[str, int, int], list[int]] = {}
    for experiment, low, high, anchor, _ in analysed:
        grouped.setdefault((experiment, low, high), []).append(anchor)

    rng = random.Random(seed)
    counts: list[int] = []
    for _ in range(iterations):
        matched = 0
        if resampling == "independent":
            for experiment, low, high, _anchor, _ in analysed:
                candidate = rng.randint(low, high)
                matched += anchor_matches(times_cache[experiment], candidate, window)
        else:
            for (experiment, low, high), anchors in grouped.items():
                size = high - low + 1
                offset = rng.randrange(size)
                times = times_cache[experiment]
                for anchor in anchors:
                    candidate = low + (anchor - low + offset) % size
                    matched += anchor_matches(times, candidate, window)
        counts.append(matched)

    total = len(analysed)
    observed_matched = sum(1 for item in analysed if item[4])
    observed_fraction = observed_matched / total
    fractions = sorted(count / total for count in counts)
    expected_fraction = statistics.fmean(fractions)
    analytic = statistics.fmean(
        analytic_match_probability(times_cache[experiment], (low, high), window)
        for experiment, low, high, _anchor, _ in analysed
    )
    at_least = sum(1 for value in fractions if value >= observed_fraction)
    below = sum(1 for value in fractions if value < observed_fraction)
    return NullSummary(
        pre_window_seconds=window.pre_seconds,
        post_window_seconds=window.post_seconds,
        anchor_domain_mode=anchor_domain_mode,
        resampling=resampling,
        seed=seed,
        iterations=iterations,
        analysed_recoveries=total,
        excluded_recoveries=sum(exclusions.values()),
        exclusion_reasons=dict(sorted(exclusions.items())),
        observed_matched=observed_matched,
        observed_fraction=observed_fraction,
        expected_fraction=expected_fraction,
        analytic_expected_fraction=analytic,
        ratio_observed_over_expected=(
            observed_fraction / expected_fraction if expected_fraction > 0 else None
        ),
        null_percentiles={
            f"p{percent:02d}": _percentile(fractions, percent)
            for percent in REPORTED_PERCENTILES
        },
        null_min=fractions[0],
        null_max=fractions[-1],
        null_stdev=statistics.pstdev(fractions),
        observed_percentile_in_null=below / iterations,
        empirical_p_value=(at_least + 1) / (iterations + 1),
        null_matched_count_histogram=dict(sorted(Counter(counts).items())),
    )
