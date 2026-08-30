"""Strict, narrowly scoped loaders for the two audited public datasets."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

import yaml


class InputFormatError(ValueError):
    """Raised when a source file violates an assumption used by the audit."""


@dataclass(frozen=True, slots=True)
class Action:
    application_epoch: int
    operation_id: str
    pairing_status: str
    start_count: int
    end_count: int
    source_reference: str


@dataclass(frozen=True, slots=True)
class TransferObservation:
    transfer_id: str
    endpoint_kind: str
    endpoint_index: int
    endpoint_id: str
    well_id: str
    requested_volume: str
    reported_actual_volume: str
    delta: str
    diverges: bool
    source_reference: str
    action_timestamp: str = ""
    evidence_timestamp: str = ""


@dataclass(frozen=True, slots=True)
class ChemspeedAudit:
    actions: tuple[Action, ...]
    transfer_observations: tuple[TransferObservation, ...]
    skipped_incomplete_endpoints: int = 0


@dataclass(frozen=True, slots=True)
class Event:
    experiment: str
    row_index: int
    time_raw: str
    property: str
    value: str
    event_category: str


@dataclass(frozen=True, slots=True)
class Recovery:
    experiment: str
    anomaly_id: str
    recovery_action: str
    perturbation_end: tuple[str, ...]
    anomaly_end: tuple[str, ...]
    anchor_time: str
    anchor_source: str


def _pairing_status(start_count: int, end_count: int) -> str:
    if start_count == 1 and end_count == 1:
        return "clean"
    if start_count == 0:
        return "missing_start"
    if end_count == 0:
        return "missing_end"
    if start_count > 1 and end_count == 1:
        return "duplicate_start"
    if start_count == 1 and end_count > 1:
        return "duplicate_end"
    return "count_mismatch"


def _parse_eventlog_lines(text: str) -> list[tuple[str, ElementTree.Element]]:
    parsed: list[tuple[str, ElementTree.Element]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        fields = raw_line.split("\t", 2)
        if len(fields) != 3:
            raise InputFormatError(
                f"Eventlog line {line_number}: expected 3 tab-separated fields, got {len(fields)}"
            )
        timestamp, _elapsed, payload = fields
        if not payload.strip():
            continue
        try:
            element = ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise InputFormatError(f"Eventlog line {line_number}: malformed XML: {exc}") from exc
        parsed.append((timestamp, element))
    return parsed


def _endpoint_rows(
    *,
    transfer_id: str,
    source_reference: str,
    start_timestamp: str,
    end_timestamp: str,
    start_element: ElementTree.Element,
    end_element: ElementTree.Element,
) -> tuple[list[TransferObservation], int]:
    observations: list[TransferObservation] = []
    skipped = 0
    for kind in ("dest", "src"):
        starts = [dict(child.attrib) for child in start_element if child.tag == kind]
        ends = [dict(child.attrib) for child in end_element if child.tag == kind]
        if len(starts) != len(ends):
            raise InputFormatError(
                f"transfer {transfer_id}: {kind} endpoint cardinality changed "
                f"from {len(starts)} to {len(ends)}"
            )
        for index, (start, end) in enumerate(zip(starts, ends, strict=True)):
            requested = start.get("volume", end.get("volume", ""))
            reported = end.get("actualVolume", "")
            if not requested or not reported:
                skipped += 1
                continue
            try:
                difference = Decimal(reported) - Decimal(requested)
            except InvalidOperation as exc:
                raise InputFormatError(
                    f"transfer {transfer_id}: non-numeric volume at {kind}[{index}]"
                ) from exc
            observations.append(
                TransferObservation(
                    transfer_id=transfer_id,
                    endpoint_kind=kind,
                    endpoint_index=index,
                    endpoint_id=start.get("id", end.get("id", "")),
                    well_id=start.get("wellid", end.get("wellid", "")),
                    requested_volume=requested,
                    reported_actual_volume=reported,
                    delta=format(difference, "f"),
                    diverges=difference != 0,
                    source_reference=source_reference,
                    action_timestamp=start_timestamp,
                    evidence_timestamp=end_timestamp,
                )
            )
    return observations, skipped


def parse_chemspeed_eventlog(text: str, source_reference: str) -> ChemspeedAudit:
    """Replay operation and transfer events without treating readback as measurement."""
    application_epoch = 0
    application_seen = False
    action_counts: dict[tuple[int, str], list[int]] = {}
    action_order: list[tuple[int, str]] = []
    transfer_starts: dict[str, tuple[str, ElementTree.Element]] = {}
    transfer_ends: dict[str, tuple[str, ElementTree.Element]] = {}

    for timestamp, element in _parse_eventlog_lines(text):
        event_type = element.attrib.get("type")
        if element.tag == "start" and event_type == "application":
            application_epoch = application_epoch + 1 if application_seen else 1
            application_seen = True
            continue
        if event_type == "operation" and element.tag in {"start", "end"}:
            operation_id = element.attrib.get("operationid")
            if not operation_id:
                raise InputFormatError("operation event is missing operationid")
            key = (application_epoch, operation_id)
            if key not in action_counts:
                action_counts[key] = [0, 0]
                action_order.append(key)
            action_counts[key][0 if element.tag == "start" else 1] += 1
            continue
        if event_type == "transfer" and element.tag in {"start", "end"}:
            transfer_id = element.attrib.get("id")
            if not transfer_id:
                raise InputFormatError("transfer event is missing id")
            target = transfer_starts if element.tag == "start" else transfer_ends
            if transfer_id in target:
                raise InputFormatError(f"duplicate transfer {element.tag}: {transfer_id}")
            target[transfer_id] = (timestamp, element)

    actions = tuple(
        Action(
            application_epoch=epoch,
            operation_id=operation_id,
            pairing_status=_pairing_status(*action_counts[(epoch, operation_id)]),
            start_count=action_counts[(epoch, operation_id)][0],
            end_count=action_counts[(epoch, operation_id)][1],
            source_reference=source_reference,
        )
        for epoch, operation_id in action_order
    )

    if set(transfer_starts) != set(transfer_ends):
        missing_end = sorted(set(transfer_starts) - set(transfer_ends))
        missing_start = sorted(set(transfer_ends) - set(transfer_starts))
        raise InputFormatError(
            f"unpaired transfers: missing_end={missing_end}, missing_start={missing_start}"
        )
    transfers: list[TransferObservation] = []
    skipped_incomplete_endpoints = 0
    for transfer_id in transfer_starts:
        start_timestamp, start_element = transfer_starts[transfer_id]
        end_timestamp, end_element = transfer_ends[transfer_id]
        rows, skipped = _endpoint_rows(
            transfer_id=transfer_id,
            source_reference=source_reference,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            start_element=start_element,
            end_element=end_element,
        )
        transfers.extend(rows)
        skipped_incomplete_endpoints += skipped
    return ChemspeedAudit(
        actions=actions,
        transfer_observations=tuple(transfers),
        skipped_incomplete_endpoints=skipped_incomplete_endpoints,
    )


def load_chemspeed_archive(path: Path) -> ChemspeedAudit:
    with zipfile.ZipFile(path) as archive:
        members = sorted(name for name in archive.namelist() if name.endswith("/Eventlog.txt"))
        if len(members) != 1:
            raise InputFormatError(
                f"expected exactly one Eventlog.txt in {path.name}, found {len(members)}"
            )
        member = members[0]
        try:
            text = archive.read(member).decode("cp1252")
        except UnicodeDecodeError as exc:
            raise InputFormatError(f"{member}: expected Windows-1252 text") from exc
    return parse_chemspeed_eventlog(text, member)


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return (str(value),)


def _experiment_key(member: str) -> str:
    parts = member.replace("\\", "/").split("/")
    if len(parts) < 3:
        raise InputFormatError(f"cannot derive experiment identity from archive member: {member}")
    return "/".join(parts[-3:]).rsplit(".", 1)[0]


def _category(raw_property: str) -> str:
    value = raw_property.strip().lower()
    if value == "critical":
        return "critical"
    if value == "warning":
        return "warning"
    if "emergency_stop" in value.replace(" ", "_"):
        return "emergency_stop"
    if value == "start process clicked":
        return "start_process"
    if value == "stop process clicked":
        return "stop_process"
    if value == "manual mode active":
        return "manual_mode"
    if "back to automatic mode" in value:
        return "automatic_mode"
    if value.startswith(("changed value", "changed_value")):
        return "changed_value"
    return "other"


def _load_events(archive: zipfile.ZipFile) -> dict[str, tuple[Event, ...]]:
    by_experiment: dict[str, tuple[Event, ...]] = {}
    for member in sorted(name for name in archive.namelist() if name.lower().endswith(".csv")):
        experiment = _experiment_key(member)
        text = archive.read(member).decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        header = next(reader, None)
        if header is None or header[:3] != ["Time", "Property", "Value"]:
            raise InputFormatError(f"{member}: unexpected CSV header {header!r}")
        events: list[Event] = []
        for row_index, row in enumerate(reader):
            time_raw = row[0] if len(row) > 0 else ""
            prop = row[1] if len(row) > 1 else ""
            value = row[2] if len(row) > 2 else ""
            events.append(
                Event(
                    experiment=experiment,
                    row_index=row_index,
                    time_raw=time_raw,
                    property=prop,
                    value=value,
                    event_category=_category(prop),
                )
            )
        by_experiment[experiment] = tuple(events)
    return by_experiment


def _load_recoveries(archive: zipfile.ZipFile) -> tuple[Recovery, ...]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    order: list[tuple[str, str, str]] = []
    for member in sorted(name for name in archive.namelist() if name.lower().endswith(".yaml")):
        experiment = _experiment_key(member)
        data = yaml.safe_load(archive.read(member).decode("utf-8")) or {}
        entries = data.get("anomalies") or []
        if not isinstance(entries, list):
            raise InputFormatError(f"{member}: anomalies must be a list")
        for entry in entries:
            if not entry or entry.get("class") in {None, "None"}:
                continue
            recovery_action = entry.get("hasRecoveryAction")
            if not recovery_action:
                continue
            key = (experiment, str(entry.get("class")), str(entry.get("id")))
            if key not in grouped:
                order.append(key)
            grouped[key].append(entry)

    recoveries: list[Recovery] = []
    for experiment, class_name, anomaly_id in order:
        entries = grouped[(experiment, class_name, anomaly_id)]
        actions = sorted({str(entry.get("hasRecoveryAction")) for entry in entries})
        perturbation_ends = sorted(
            {
                value
                for entry in entries
                for value in _as_tuple((entry.get("PerturbationMode") or {}).get("hasEnd"))
                if value not in {"", "0", "00:00:00"}
            }
        )
        anomaly_ends = sorted(
            {value for entry in entries for value in _as_tuple(entry.get("hasEnd")) if value}
        )
        if len(actions) != 1:
            raise InputFormatError(f"{experiment}::{anomaly_id}: ambiguous recovery action")
        if len(perturbation_ends) == 1:
            anchor_time, anchor_source = perturbation_ends[0], "perturbation_end"
        elif len(perturbation_ends) > 1:
            raise InputFormatError(f"{experiment}::{anomaly_id}: ambiguous perturbation end")
        elif len(anomaly_ends) == 1:
            anchor_time, anchor_source = anomaly_ends[0], "anomaly_end"
        else:
            raise InputFormatError(f"{experiment}::{anomaly_id}: no unambiguous anchor")
        recoveries.append(
            Recovery(
                experiment=experiment,
                anomaly_id=anomaly_id,
                recovery_action=actions[0],
                perturbation_end=tuple(perturbation_ends),
                anomaly_end=tuple(anomaly_ends),
                anchor_time=anchor_time,
                anchor_source=anchor_source,
            )
        )
    return tuple(recoveries)


def load_batch_distillation(
    metadata_archive: Path, operation_logs_archive: Path
) -> tuple[
    tuple[Recovery, ...],
    dict[str, tuple[Event, ...]],
    tuple[Recovery, ...],
]:
    with zipfile.ZipFile(metadata_archive) as metadata_zip:
        recoveries = _load_recoveries(metadata_zip)
    with zipfile.ZipFile(operation_logs_archive) as logs_zip:
        events = _load_events(logs_zip)
    included = tuple(recovery for recovery in recoveries if recovery.experiment in events)
    excluded = tuple(recovery for recovery in recoveries if recovery.experiment not in events)
    return included, events, excluded
