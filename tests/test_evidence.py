"""Tests for the evidence record and timestamp parsing."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from chronoguard.evidence import EvidenceRecord, parse_timestamp

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


class TestParseTimestamp:
    def test_aware_datetime_passes_through(self) -> None:
        dt = datetime(2023, 6, 1, 12, 0, tzinfo=UTC)
        assert parse_timestamp(dt) == dt

    def test_iso_string_with_offset(self) -> None:
        assert parse_timestamp("2023-06-01T12:00:00+05:30") == datetime(
            2023, 6, 1, 12, 0, tzinfo=IST
        )

    def test_iso_string_with_trailing_z(self) -> None:
        assert parse_timestamp("2023-06-01T12:00:00Z") == datetime(2023, 6, 1, 12, 0, tzinfo=UTC)

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        assert parse_timestamp("  2023-06-01T12:00:00Z  ") == datetime(
            2023, 6, 1, 12, 0, tzinfo=UTC
        )

    def test_epoch_seconds(self) -> None:
        assert parse_timestamp(1685620800) == datetime(2023, 6, 1, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        "value",
        ["yesterday", "not a date", "2023-13-45", "", "   ", None, True, False, object()],
    )
    def test_unusable_values_give_none(self, value: object) -> None:
        assert parse_timestamp(value) is None

    def test_naive_datetime_is_rejected_by_default(self) -> None:
        assert parse_timestamp(datetime(2023, 6, 1, 12, 0)) is None

    def test_date_only_string_is_rejected_by_default(self) -> None:
        # A bare date has no offset, so it isn't a point on the timeline.
        assert parse_timestamp("2023-06-01") is None

    def test_naive_values_accepted_when_a_timezone_is_assumed(self) -> None:
        assert parse_timestamp("2023-06-01", assume_tz=UTC) == datetime(2023, 6, 1, tzinfo=UTC)
        assert parse_timestamp(datetime(2023, 6, 1), assume_tz=IST) == datetime(
            2023, 6, 1, tzinfo=IST
        )
        assert parse_timestamp(date(2023, 6, 1), assume_tz=UTC) == datetime(2023, 6, 1, tzinfo=UTC)

    def test_assume_tz_does_not_override_an_explicit_offset(self) -> None:
        parsed = parse_timestamp("2023-06-01T12:00:00+05:30", assume_tz=UTC)
        assert parsed == datetime(2023, 6, 1, 12, 0, tzinfo=IST)


class TestEvidenceRecord:
    def test_strict_construction_requires_an_aware_datetime(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceRecord(
                content="x",
                source_id="s",
                published_at=datetime(2023, 6, 1, 12, 0),
            )

    def test_strict_construction_accepts_aware_datetime(self) -> None:
        rec = EvidenceRecord(
            content="x", source_id="s", published_at=datetime(2023, 6, 1, tzinfo=UTC)
        )
        assert rec.is_dated
        assert rec.undated_reason is None

    def test_from_source_keeps_junk_timestamps_for_reporting(self) -> None:
        rec = EvidenceRecord.from_source("x", "s", published_at="sometime last week")
        assert rec.published_at is None
        assert rec.published_at_raw == "sometime last week"
        assert not rec.is_dated
        assert "could not parse" in (rec.undated_reason or "")

    def test_from_source_with_no_timestamp_reports_it_as_missing(self) -> None:
        rec = EvidenceRecord.from_source("x", "s")
        assert rec.published_at is None
        assert rec.published_at_raw is None
        assert rec.undated_reason == "no publication timestamp"

    def test_from_source_never_raises_on_bad_input(self) -> None:
        rec = EvidenceRecord.from_source("x", "s", published_at=object(), retrieved_at="nope")
        assert rec.published_at is None
        assert rec.retrieved_at is None

    def test_metadata_defaults_are_not_shared_between_records(self) -> None:
        a = EvidenceRecord.from_source("a", "s1")
        b = EvidenceRecord.from_source("b", "s2")
        a.metadata["seen"] = True
        assert b.metadata == {}

    def test_metadata_is_copied_not_aliased(self) -> None:
        shared = {"rank": 1}
        rec = EvidenceRecord.from_source("a", "s", metadata=shared)
        shared["rank"] = 99
        assert rec.metadata == {"rank": 1}

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceRecord(content="x", source_id="s", typo_field=1)
