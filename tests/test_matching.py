from __future__ import annotations

from lab_log_audit.load import Event, Recovery
from lab_log_audit.matching import Window, match_recovery, parse_time_of_day


def _event(time: str, row: int) -> Event:
    return Event(
        experiment="exp",
        row_index=row,
        time_raw=time,
        property="event",
        value="-",
        event_category="other",
    )


def _recovery(**overrides: object) -> Recovery:
    values: dict[str, object] = {
        "experiment": "exp",
        "anomaly_id": "A1",
        "recovery_action": "Restore normal state",
        "perturbation_end": ("12:10:00",),
        "anomaly_end": ("12:11:00",),
        "anchor_time": "12:10:00",
        "anchor_source": "perturbation_end",
    }
    values.update(overrides)
    return Recovery(**values)  # type: ignore[arg-type]


def test_parse_time_of_day_rejects_non_hh_mm_ss() -> None:
    assert parse_time_of_day("12:10:03") == 43_803
    assert parse_time_of_day("12:10") is None


def test_window_boundaries_are_inclusive_and_preserve_source_order() -> None:
    events = [
        _event("12:12:01", 4),
        _event("12:09:00", 0),
        _event("12:12:00", 3),
        _event("12:08:59", 1),
        _event("12:10:00", 2),
    ]

    matched = match_recovery(_recovery(), events, Window(60, 120))

    assert [event.row_index for event in matched.events] == [0, 3, 2]


def test_unparseable_anchor_fails_loudly() -> None:
    recovery = _recovery(anchor_time="not-a-time")

    try:
        match_recovery(recovery, [], Window(60, 120))
    except ValueError as exc:
        assert "anchor" in str(exc)
    else:
        raise AssertionError("unparseable recovery anchor was accepted")


def test_empty_event_timestamps_are_not_matches() -> None:
    events = [_event("", 0), _event("12:10", 1), _event("12:10:00", 2)]

    matched = match_recovery(_recovery(), events, Window(60, 120))

    assert [event.row_index for event in matched.events] == [2]

