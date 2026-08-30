"""Explicit temporal matching for labelled recoveries and operation-log events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .load import Event, Recovery


@dataclass(frozen=True, slots=True)
class Window:
    pre_seconds: int
    post_seconds: int

    def __post_init__(self) -> None:
        if self.pre_seconds < 0 or self.post_seconds < 0:
            raise ValueError("window widths must be non-negative")


@dataclass(frozen=True, slots=True)
class RecoveryMatch:
    recovery: Recovery
    events: tuple[Event, ...]
    window: Window


def parse_time_of_day(raw: str) -> int | None:
    try:
        parsed = datetime.strptime(raw.strip(), "%H:%M:%S").time()
    except ValueError:
        return None
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def match_recovery(recovery: Recovery, events: tuple[Event, ...] | list[Event], window: Window) -> RecoveryMatch:
    anchor = parse_time_of_day(recovery.anchor_time)
    if anchor is None:
        raise ValueError(f"unparseable recovery anchor: {recovery.anchor_time!r}")
    start = anchor - window.pre_seconds
    end = anchor + window.post_seconds
    matched: list[Event] = []
    for event in events:
        event_time = parse_time_of_day(event.time_raw)
        if event_time is not None and start <= event_time <= end:
            matched.append(event)
    return RecoveryMatch(recovery=recovery, events=tuple(matched), window=window)


def match_all(
    recoveries: tuple[Recovery, ...],
    events_by_experiment: dict[str, tuple[Event, ...]],
    window: Window,
) -> tuple[RecoveryMatch, ...]:
    return tuple(
        match_recovery(recovery, events_by_experiment[recovery.experiment], window)
        for recovery in recoveries
    )

