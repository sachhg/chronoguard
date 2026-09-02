"""Tests for TemporalGuard.

The boundary rule under test: a record is allowed when published_at < as_of,
strictly. Exactly-at-as_of is rejected. See the module docstring in guard.py for
why.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import (
    FilterResult,
    GuardPolicy,
    Judgement,
    TemporalGuard,
    Verdict,
    guard_records,
)

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))
PST = timezone(timedelta(hours=-8))

AS_OF = datetime(2023, 6, 1, tzinfo=UTC)


def rec(source_id: str, published_at: object = None, content: str = "body") -> EvidenceRecord:
    return EvidenceRecord.from_source(content, source_id, published_at=published_at)


class TestConstruction:
    def test_naive_as_of_is_refused(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            TemporalGuard(datetime(2023, 6, 1))

    def test_bare_date_string_as_of_is_refused(self) -> None:
        with pytest.raises(ValueError, match="explicit offset"):
            TemporalGuard("2023-06-01")

    def test_iso_string_as_of_is_accepted(self) -> None:
        assert TemporalGuard("2023-06-01T00:00:00Z").as_of == AS_OF

    def test_policy_accepts_a_plain_string(self) -> None:
        assert TemporalGuard(AS_OF, policy="warn").policy is GuardPolicy.WARN

    def test_unknown_policy_is_refused(self) -> None:
        with pytest.raises(ValueError):
            TemporalGuard(AS_OF, policy="lenient")

    def test_defaults_are_conservative(self) -> None:
        guard = TemporalGuard(AS_OF)
        assert guard.policy is GuardPolicy.STRICT
        assert guard.allow_undated is False

    def test_repr_is_readable(self) -> None:
        assert "2023-06-01" in repr(TemporalGuard(AS_OF))


class TestBoundary:
    """The exact-instant rule, which is the whole ballgame."""

    def test_record_published_exactly_at_as_of_is_rejected(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("exact", AS_OF))
        assert judgement.verdict is Verdict.FUTURE
        assert judgement.kept is False
        assert "boundary is exclusive" in judgement.reason

    def test_one_microsecond_before_as_of_is_allowed(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("just-before", AS_OF - timedelta(microseconds=1)))
        assert judgement.verdict is Verdict.ALLOWED
        assert judgement.kept is True

    def test_one_microsecond_after_as_of_is_rejected(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("just-after", AS_OF + timedelta(microseconds=1)))
        assert judgement.verdict is Verdict.FUTURE
        assert judgement.kept is False

    def test_shifting_as_of_forward_admits_the_boundary_record(self) -> None:
        # The documented escape hatch: want a whole day, name the next midnight.
        guard = TemporalGuard(datetime(2023, 6, 2, tzinfo=UTC))
        assert guard.allows(rec("exact", AS_OF))


class TestTimezones:
    def test_records_are_compared_as_instants_not_wall_clocks(self) -> None:
        # 05:00 in IST is 23:30Z the previous day, so it predates a UTC midnight
        # as_of even though its wall clock reads later.
        early = rec("ist-early", datetime(2023, 6, 1, 5, 0, tzinfo=IST))
        assert TemporalGuard(AS_OF).allows(early) is True

    def test_wall_clock_earlier_but_instant_later_is_still_rejected(self) -> None:
        # 20:00 on May 31st in PST is 04:00Z on June 1st, which is after as_of.
        late = rec("pst-late", datetime(2023, 5, 31, 20, 0, tzinfo=PST))
        assert TemporalGuard(AS_OF).allows(late) is False

    def test_as_of_in_a_non_utc_zone_behaves_the_same(self) -> None:
        # Same instant as AS_OF, expressed in IST.
        guard = TemporalGuard(datetime(2023, 6, 1, 5, 30, tzinfo=IST))
        assert guard.allows(rec("before", AS_OF - timedelta(seconds=1))) is True
        assert guard.allows(rec("after", AS_OF + timedelta(seconds=1))) is False

    def test_mixed_timezone_batch_orders_correctly(self) -> None:
        records = [
            rec("utc-before", datetime(2023, 5, 31, 23, 0, tzinfo=UTC)),
            rec("ist-before", datetime(2023, 6, 1, 4, 0, tzinfo=IST)),
            rec("pst-after", datetime(2023, 5, 31, 20, 0, tzinfo=PST)),
            rec("utc-after", datetime(2023, 6, 2, tzinfo=UTC)),
        ]
        result = TemporalGuard(AS_OF).filter(records)
        assert [r.source_id for r in result.kept] == ["utc-before", "ist-before"]
        assert [r.source_id for r in result.dropped] == ["pst-after", "utc-after"]


class TestUndatedRecords:
    def test_missing_timestamp_is_rejected_by_default(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("nodate"))
        assert judgement.verdict is Verdict.UNDATED
        assert judgement.kept is False
        assert "can't be shown to predate" in judgement.reason

    def test_unparseable_timestamp_is_rejected_by_default(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("junk", "last tuesday"))
        assert judgement.verdict is Verdict.UNPARSEABLE
        assert judgement.kept is False
        assert "last tuesday" in judgement.reason

    def test_naive_timestamp_counts_as_unparseable(self) -> None:
        judgement = TemporalGuard(AS_OF).judge(rec("naive", "2023-05-01T00:00:00"))
        assert judgement.verdict is Verdict.UNPARSEABLE
        assert judgement.kept is False

    def test_allow_undated_admits_both_flavours(self) -> None:
        guard = TemporalGuard(AS_OF, allow_undated=True)
        missing = guard.judge(rec("nodate"))
        junk = guard.judge(rec("junk", "last tuesday"))
        assert missing.kept is True
        assert junk.kept is True
        assert "allow_undated is on" in missing.reason

    def test_allow_undated_does_not_admit_future_records(self) -> None:
        guard = TemporalGuard(AS_OF, allow_undated=True)
        assert guard.allows(rec("future", datetime(2023, 7, 1, tzinfo=UTC))) is False

    def test_allowed_undated_records_still_count_as_violations(self) -> None:
        guard = TemporalGuard(AS_OF, allow_undated=True)
        result = guard.filter([rec("nodate")])
        assert result.kept_count == 1
        assert result.filtered_count == 0
        assert result.violation_count == 1


class TestPolicies:
    def test_strict_drops_violations(self) -> None:
        records = [rec("old", datetime(2023, 1, 1, tzinfo=UTC)), rec("new", datetime(2024, 1, 1, tzinfo=UTC))]
        result = TemporalGuard(AS_OF, policy=GuardPolicy.STRICT).filter(records)
        assert [r.source_id for r in result.kept] == ["old"]
        assert result.filtered_count == 1

    def test_warn_keeps_violations_but_flags_them(self) -> None:
        records = [rec("old", datetime(2023, 1, 1, tzinfo=UTC)), rec("new", datetime(2024, 1, 1, tzinfo=UTC))]
        result = TemporalGuard(AS_OF, policy=GuardPolicy.WARN).filter(records)
        assert [r.source_id for r in result.kept] == ["old", "new"]
        assert result.filtered_count == 0
        assert result.violation_count == 1
        assert result.violations[0].record.source_id == "new"

    def test_warn_keeps_undated_records_too(self) -> None:
        result = TemporalGuard(AS_OF, policy=GuardPolicy.WARN).filter([rec("nodate")])
        assert result.kept_count == 1
        assert result.violations[0].verdict is Verdict.UNDATED

    def test_warn_and_strict_agree_on_verdicts_and_differ_only_on_keeping(self) -> None:
        records = [
            rec("old", datetime(2023, 1, 1, tzinfo=UTC)),
            rec("new", datetime(2024, 1, 1, tzinfo=UTC)),
            rec("nodate"),
        ]
        strict = TemporalGuard(AS_OF, policy="strict").filter(records)
        warn = TemporalGuard(AS_OF, policy="warn").filter(records)
        assert [j.verdict for j in strict.judgements] == [j.verdict for j in warn.judgements]
        assert strict.kept_count == 1
        assert warn.kept_count == 3


class TestFilterResult:
    def test_empty_input(self) -> None:
        result = TemporalGuard(AS_OF).filter([])
        assert result.judgements == []
        assert result.kept == []
        assert result.dropped == []
        assert result.total == 0
        assert result.kept_count == 0
        assert result.filtered_count == 0
        assert result.violation_count == 0
        assert result.counts == {v.value: 0 for v in Verdict}
        assert "kept 0/0" in result.summary()

    def test_everything_violates(self) -> None:
        records = [
            rec("a", datetime(2023, 6, 2, tzinfo=UTC)),
            rec("b", AS_OF),
            rec("c", "garbage"),
            rec("d"),
        ]
        result = TemporalGuard(AS_OF).filter(records)
        assert result.kept == []
        assert result.filtered_count == 4
        assert result.violation_count == 4
        assert result.counts["allowed"] == 0

    def test_nothing_violates(self) -> None:
        records = [rec("a", datetime(2022, 1, 1, tzinfo=UTC)), rec("b", datetime(2023, 5, 31, tzinfo=UTC))]
        result = TemporalGuard(AS_OF).filter(records)
        assert result.kept_count == 2
        assert result.filtered_count == 0
        assert result.violations == []

    def test_counts_cover_every_verdict(self) -> None:
        records = [
            rec("allowed", datetime(2023, 1, 1, tzinfo=UTC)),
            rec("future", datetime(2024, 1, 1, tzinfo=UTC)),
            rec("undated"),
            rec("unparseable", "nope"),
        ]
        counts = TemporalGuard(AS_OF).filter(records).counts
        assert counts == {"allowed": 1, "future": 1, "undated": 1, "unparseable": 1}

    def test_judgement_order_matches_input_order(self) -> None:
        records = [rec(f"s{i}", datetime(2023, 1, 1, tzinfo=UTC)) for i in range(5)]
        result = TemporalGuard(AS_OF).filter(records)
        assert [j.record.source_id for j in result.judgements] == [f"s{i}" for i in range(5)]

    def test_summary_reports_the_filtered_count(self) -> None:
        records = [rec("old", datetime(2023, 1, 1, tzinfo=UTC)), rec("new", datetime(2024, 1, 1, tzinfo=UTC))]
        summary = TemporalGuard(AS_OF).filter(records).summary()
        assert "kept 1/2" in summary
        assert "filtered 1" in summary
        assert "future=1" in summary

    def test_accepts_any_iterable_not_just_lists(self) -> None:
        records = (rec(f"s{i}", datetime(2023, 1, 1, tzinfo=UTC)) for i in range(3))
        assert TemporalGuard(AS_OF).filter(records).kept_count == 3

    def test_is_violation_is_independent_of_kept(self) -> None:
        judgement = Judgement(
            record=rec("x", datetime(2024, 1, 1, tzinfo=UTC)),
            verdict=Verdict.FUTURE,
            kept=True,
            reason="warn policy",
        )
        assert judgement.is_violation is True
        assert judgement.kept is True


class TestConvenienceWrapper:
    def test_guard_records_matches_the_class(self) -> None:
        records = [rec("old", datetime(2023, 1, 1, tzinfo=UTC)), rec("new", datetime(2024, 1, 1, tzinfo=UTC))]
        result = guard_records(records, "2023-06-01T00:00:00Z")
        assert isinstance(result, FilterResult)
        assert [r.source_id for r in result.kept] == ["old"]

    def test_guard_records_forwards_options(self) -> None:
        result = guard_records([rec("nodate")], AS_OF, allow_undated=True)
        assert result.kept_count == 1


class TestRetrievedAt:
    def test_retrieved_at_is_not_filtered_on(self) -> None:
        # Normal case: you're running the backtest today, so everything was
        # retrieved long after as_of. That must not matter.
        record = EvidenceRecord.from_source(
            "body",
            "s",
            published_at="2023-01-01T00:00:00Z",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        assert TemporalGuard(AS_OF).allows(record) is True
