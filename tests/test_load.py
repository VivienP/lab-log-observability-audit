from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from lab_log_audit.load import (
    InputFormatError,
    _category,
    anomaly_census,
    load_batch_distillation,
    load_chemspeed_archive,
    parse_chemspeed_eventlog,
)


def _line(seconds: int, payload: str) -> str:
    return f"2026-01-01 12:00:{seconds:02d}.000\t{seconds}\t{payload}\n"


def test_chemspeed_operation_identity_includes_application_epoch() -> None:
    text = "".join(
        [
            _line(0, '<start type="application" mode="real"/>'),
            _line(1, '<start type="operation" operationid="1"><Move/></start>'),
            _line(2, '<end type="operation" operationid="1"><Move/></end>'),
            _line(3, '<start type="application" mode="real"/>'),
            _line(4, '<start type="operation" operationid="1"><Move/></start>'),
            _line(5, '<end type="operation" operationid="1"><Move/></end>'),
        ]
    )

    audit = parse_chemspeed_eventlog(text, source_reference="Eventlog.txt")

    assert [(a.application_epoch, a.operation_id, a.pairing_status) for a in audit.actions] == [
        (1, "1", "clean"),
        (2, "1", "clean"),
    ]


def test_chemspeed_transfer_endpoints_are_positional_not_id_keyed() -> None:
    start = (
        '<start type="transfer" id="T1">'
        '<src id="0" wellid="W1" volume="0.1"/>'
        '<src id="0" wellid="W2" volume="0.2"/>'
        "</start>"
    )
    end = (
        '<end type="transfer" id="T1">'
        '<src id="0" wellid="W1" volume="0.1" actualVolume="0.1"/>'
        '<src id="0" wellid="W2" volume="0.2" actualVolume="0.2"/>'
        "</end>"
    )

    audit = parse_chemspeed_eventlog(_line(0, start) + _line(1, end), "Eventlog.txt")

    assert [row.endpoint_index for row in audit.transfer_observations] == [0, 1]
    assert [row.delta for row in audit.transfer_observations] == ["0.0", "0.0"]


def test_chemspeed_malformed_fragment_is_not_silently_dropped() -> None:
    with pytest.raises(InputFormatError, match="line 1"):
        parse_chemspeed_eventlog(_line(0, "<broken>"), "Eventlog.txt")


def test_chemspeed_duplicate_transfer_id_fails_loudly() -> None:
    start = '<start type="transfer" id="T1"><src id="0" wellid="W1" volume="0.1"/></start>'
    end = (
        '<end type="transfer" id="T1">'
        '<src id="0" wellid="W1" volume="0.1" actualVolume="0.1"/></end>'
    )
    duplicate_start = (
        '<start type="transfer" id="T1"><src id="0" wellid="W2" volume="0.2"/></start>'
    )
    with pytest.raises(InputFormatError, match="duplicate transfer start"):
        parse_chemspeed_eventlog(
            _line(0, start) + _line(1, duplicate_start) + _line(2, end),
            "Eventlog.txt",
        )


def test_chemspeed_counts_skipped_incomplete_endpoints() -> None:
    start = (
        '<start type="transfer" id="T1">'
        '<src id="0" wellid="W1" volume="0.1"/>'
        '<src id="0" wellid="W2" volume="0.2"/>'
        "</start>"
    )
    end = (
        '<end type="transfer" id="T1">'
        '<src id="0" wellid="W1" volume="0.1" actualVolume="0.1"/>'
        '<src id="0" wellid="W2" volume="0.2"/>'
        "</end>"
    )

    audit = parse_chemspeed_eventlog(_line(0, start) + _line(1, end), "Eventlog.txt")

    assert len(audit.transfer_observations) == 1
    assert audit.skipped_incomplete_endpoints == 1
    assert audit.transfer_observations[0].well_id == "W1"


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)


def test_chemspeed_archive_preserves_windows_1252_degree_symbols(tmp_path: Path) -> None:
    archive_path = tmp_path / "flex.zip"
    payload = (
        '2026-01-01 12:00:00\t0\t<start type="operation" operationid="1">'
        '<Heat><temperatureunit>°C</temperatureunit></Heat></start>\n'
        '2026-01-01 12:00:01\t1\t<end type="operation" operationid="1">'
        '<Heat><temperatureunit>°C</temperatureunit></Heat></end>\n'
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Flex/Chemspeed/run/Eventlog.txt", payload.encode("cp1252"))

    audit = load_chemspeed_archive(archive_path)

    assert audit.actions[0].pairing_status == "clean"


def test_batch_loader_deduplicates_sensor_annotations_and_pairs_by_path(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.zip"
    logs = tmp_path / "logs.zip"
    yaml_text = """
anomalies:
  - class: ConfirmedAnomaly
    id: A1
    hasEnd: '12:20:00'
    hasRecoveryAction: Restore normal state
    PerturbationMode:
      hasEnd: '12:10:00'
  - class: ConfirmedAnomaly
    id: A1
    hasEnd: '12:20:00'
    hasRecoveryAction: Restore normal state
    PerturbationMode:
      hasEnd: '12:10:00'
"""
    _write_zip(
        metadata,
        {"metadata/Operation/mixture/op/test_anormal_experiment_001.yaml": yaml_text},
    )
    _write_zip(
        logs,
        {"logs/mixture/op/test_anormal_experiment_001.csv": "Time,Property,Value\n12:10:01,Changed value P1,2\n"},
    )

    recoveries, events, excluded = load_batch_distillation(metadata, logs)

    assert len(recoveries) == 1
    assert recoveries[0].anchor_time == "12:10:00"
    assert recoveries[0].anchor_source == "perturbation_end"
    assert len(events[recoveries[0].experiment]) == 1
    assert excluded == ()


def test_batch_loader_uses_anomaly_end_only_as_explicit_fallback(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.zip"
    logs = tmp_path / "logs.zip"
    _write_zip(
        metadata,
        {
            "metadata/Operation/mix/op/exp.yaml": """
anomalies:
  - class: ConfirmedAnomaly
    id: A1
    hasEnd: '12:20:00'
    hasRecoveryAction: Restore normal state
    PerturbationMode: {}
"""
        },
    )
    _write_zip(logs, {"logs/mix/op/exp.csv": "Time,Property,Value\n12:20:00,Event,-\n"})

    recoveries, _, excluded = load_batch_distillation(metadata, logs)

    assert recoveries[0].anchor_time == "12:20:00"
    assert recoveries[0].anchor_source == "anomaly_end"
    assert excluded == ()


def test_batch_loader_reports_recoveries_excluded_for_missing_operation_log(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata.zip"
    logs = tmp_path / "logs.zip"
    _write_zip(
        metadata,
        {
            "metadata/Operation/mix/op/with_log.yaml": """
anomalies:
  - class: ConfirmedAnomaly
    id: INCLUDED
    hasEnd: '12:20:00'
    hasRecoveryAction: restore
    PerturbationMode: {}
""",
            "metadata/Operation/mix/op/no_log.yaml": """
anomalies:
  - class: ConfirmedAnomaly
    id: EXCLUDED
    hasEnd: '12:30:00'
    hasRecoveryAction: restore
    PerturbationMode: {}
""",
        },
    )
    _write_zip(logs, {"logs/mix/op/with_log.csv": "Time,Property,Value\n12:20:00,Event,-\n"})

    recoveries, _, excluded = load_batch_distillation(metadata, logs)

    assert [item.anomaly_id for item in recoveries] == ["INCLUDED"]
    assert [item.anomaly_id for item in excluded] == ["EXCLUDED"]


@pytest.mark.parametrize(
    ("raw_property", "expected"),
    [
        # Both source spellings of the same event must land in one category. Matching
        # the raw text used to send "emergency _stop" and "Automatic mode active" to
        # "other", which would mislead anyone filtering on event_category.
        ("Emergency Stop", "emergency_stop"),
        ("emergency _stop", "emergency_stop"),
        ("Automatic mode active", "automatic_mode"),
        ("AV8 back to automatic mode", "automatic_mode"),
        ("Extraction Pumps back to automatic mode.", "automatic_mode"),
        ("Manual mode active", "manual_mode"),
        ("Changed value P702", "changed_value"),
        ("changed_value_AV709", "changed_value"),
        ("Start process clicked", "start_process"),
        ("Stop process clicked", "stop_process"),
        ("Critical", "critical"),
        ("Warning", "warning"),
        ("Executing step", "other"),
        ("P301 new step", "other"),
        ("", "other"),
    ],
)
def test_event_category_normalises_source_spelling_variants(
    raw_property: str, expected: str
) -> None:
    assert _category(raw_property) == expected


def test_anomaly_census_counts_records_the_recovery_filter_drops(tmp_path: Path) -> None:
    """The census must see records with no recovery action and no anomaly class."""
    metadata = tmp_path / "metadata.zip"
    _write_zip(
        metadata,
        {
            "metadata/Operation/mix/op/exp.yaml": """
anomalies:
  - class: ConfirmedAnomaly
    id: A1
    hasRecoveryAction: restore
  - class: ConfirmedAnomaly
    id: A1
    hasRecoveryAction: restore
  - class: Anomaly
    id: A2
  - class: None
    id: A3
"""
        },
    )

    census = anomaly_census(metadata)

    # A1 is deduplicated; A2 has no recovery action and A3 no class, so neither
    # reaches load_batch_distillation, yet both are real records.
    assert census["deduplicated_total"] == 3
    assert census["with_anomaly_class"] == 2
    assert census["without_anomaly_class"] == 1
    assert census["confirmed_anomaly"] == 1
    assert census["by_class"] == {"Anomaly": 1, "ConfirmedAnomaly": 1, "None": 1}
