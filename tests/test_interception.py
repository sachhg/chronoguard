"""Tests for tool-call interception.

Uses tiny inline tools rather than the shipped fixtures so these stay fast and
independent of corpus content.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import GuardPolicy, TemporalGuard, Verdict
from chronoguard.interception import (
    AuditLog,
    CallableAdapter,
    GuardedTool,
    MappingAdapter,
    RecordAdapter,
    ToolCall,
    guard_tool,
    guarded_tool,
    resolve_adapter,
)

UTC = timezone.utc
AS_OF = "2023-06-01T00:00:00Z"

BEFORE = "2023-01-15T00:00:00Z"
AFTER = "2023-09-15T00:00:00Z"


@pytest.fixture
def guard() -> TemporalGuard:
    return TemporalGuard(AS_OF)


@pytest.fixture
def audit() -> AuditLog:
    return AuditLog()


def hits() -> list[dict]:
    """Raw output with one of every interesting case."""
    return [
        {"url": "u1", "title": "Before", "snippet": "old news", "date": BEFORE, "rank": 1},
        {"url": "u2", "title": "After", "snippet": "FUTURE-TOKEN", "date": AFTER, "rank": 2},
        {"url": "u3", "title": "Boundary", "snippet": "edge", "date": AS_OF, "rank": 3},
        {"url": "u4", "title": "Undated", "snippet": "no date", "rank": 4},
        {"url": "u5", "title": "Junk", "snippet": "bad date", "date": "last spring", "rank": 5},
    ]


WEB_ADAPTER = MappingAdapter(
    content_key=("title", "snippet"),
    source_key="url",
    published_key="date",
)


class TestRecordAdapter:
    def test_passes_a_list_of_records_through(self) -> None:
        records = [EvidenceRecord.from_source("a", "s1"), EvidenceRecord.from_source("b", "s2")]
        assert RecordAdapter().to_records(records) == records

    def test_wraps_a_single_record(self) -> None:
        record = EvidenceRecord.from_source("a", "s1")
        assert RecordAdapter().to_records(record) == [record]

    def test_none_is_empty(self) -> None:
        assert RecordAdapter().to_records(None) == []

    def test_dicts_get_a_pointed_error(self) -> None:
        with pytest.raises(TypeError, match="MappingAdapter"):
            RecordAdapter().to_records([{"content": "a"}])

    def test_scalars_are_refused(self) -> None:
        with pytest.raises(TypeError):
            RecordAdapter().to_records(42)


class TestCallableAdapter:
    def test_plain_function_is_usable_as_an_adapter(self) -> None:
        def to_records(raw: list[str]) -> list[EvidenceRecord]:
            return [EvidenceRecord.from_source(t, f"s{i}", published_at=BEFORE) for i, t in enumerate(raw)]

        records = CallableAdapter(to_records).to_records(["a", "b"])
        assert [r.content for r in records] == ["a", "b"]


class TestMappingAdapter:
    def test_maps_the_named_fields(self) -> None:
        records = WEB_ADAPTER.to_records(hits())
        assert [r.source_id for r in records] == ["u1", "u2", "u3", "u4", "u5"]
        assert records[0].content == "Before\nold news"
        assert records[0].published_at == datetime(2023, 1, 15, tzinfo=UTC)

    def test_leftover_fields_become_metadata(self) -> None:
        records = WEB_ADAPTER.to_records(hits())
        assert records[0].metadata == {"rank": 1}

    def test_explicit_metadata_keys_win(self) -> None:
        adapter = MappingAdapter(content_key="title", source_key="url", metadata_keys=["rank"])
        assert adapter.to_records([{"url": "u", "title": "t", "rank": 9, "junk": "x"}])[0].metadata == {
            "rank": 9
        }

    def test_missing_content_fields_are_skipped_not_stringified(self) -> None:
        adapter = MappingAdapter(content_key=("title", "snippet"), source_key="url")
        assert adapter.to_records([{"url": "u", "title": "only title"}])[0].content == "only title"

    def test_source_key_falls_back_through_candidates(self) -> None:
        adapter = MappingAdapter(content_key="content")
        assert adapter.to_records([{"content": "c", "id": "the-id"}])[0].source_id == "the-id"

    def test_missing_source_id_gets_a_positional_placeholder(self) -> None:
        adapter = MappingAdapter(content_key="content")
        records = adapter.to_records([{"content": "a"}, {"content": "b"}])
        assert [r.source_id for r in records] == ["record-0", "record-1"]

    def test_unparseable_date_is_carried_through_for_reporting(self) -> None:
        junk = WEB_ADAPTER.to_records(hits())[4]
        assert junk.published_at is None
        assert junk.published_at_raw == "last spring"

    def test_results_key_digs_into_a_wrapper_dict(self) -> None:
        adapter = MappingAdapter(content_key="content", results_key="results")
        payload = {"query": "x", "results": [{"content": "a", "id": "s1"}]}
        assert [r.source_id for r in adapter.to_records(payload)] == ["s1"]

    def test_a_bare_mapping_is_treated_as_one_result(self) -> None:
        adapter = MappingAdapter(content_key="content")
        assert len(adapter.to_records({"content": "a", "id": "s1"})) == 1

    def test_assume_tz_rescues_naive_timestamps(self) -> None:
        adapter = MappingAdapter(content_key="content", published_key="date", assume_tz=UTC)
        record = adapter.to_records([{"content": "a", "id": "s", "date": "2023-05-01"}])[0]
        assert record.published_at == datetime(2023, 5, 1, tzinfo=UTC)

    def test_naive_timestamps_stay_unusable_without_assume_tz(self) -> None:
        adapter = MappingAdapter(content_key="content", published_key="date")
        record = adapter.to_records([{"content": "a", "id": "s", "date": "2023-05-01"}])[0]
        assert record.published_at is None

    def test_records_pass_straight_through(self) -> None:
        record = EvidenceRecord.from_source("a", "s")
        assert MappingAdapter().to_records([record]) == [record]

    def test_empty_and_none_inputs(self) -> None:
        assert MappingAdapter().to_records([]) == []
        assert MappingAdapter().to_records(None) == []

    def test_non_mapping_items_are_refused(self) -> None:
        with pytest.raises(TypeError, match="dict-shaped"):
            MappingAdapter().to_records(["just a string"])


class TestResolveAdapter:
    def test_none_gives_the_record_adapter(self) -> None:
        assert isinstance(resolve_adapter(None), RecordAdapter)

    def test_an_adapter_is_returned_as_is(self) -> None:
        adapter = MappingAdapter()
        assert resolve_adapter(adapter) is adapter

    def test_a_callable_is_wrapped(self) -> None:
        assert isinstance(resolve_adapter(lambda raw: []), CallableAdapter)

    def test_junk_is_refused(self) -> None:
        with pytest.raises(TypeError):
            resolve_adapter(42)


class TestGuardedTool:
    def test_agent_never_sees_post_as_of_content(self, guard: TemporalGuard) -> None:
        tool = guard_tool(hits, guard, WEB_ADAPTER)
        blob = " ".join(r.content for r in tool())
        assert "FUTURE-TOKEN" not in blob
        assert "old news" in blob

    def test_only_the_pre_as_of_record_survives(self, guard: TemporalGuard) -> None:
        tool = guard_tool(hits, guard, WEB_ADAPTER)
        assert [r.source_id for r in tool()] == ["u1"]

    def test_boundary_and_undated_hits_are_dropped_too(self, guard: TemporalGuard) -> None:
        tool = guard_tool(hits, guard, WEB_ADAPTER)
        tool()
        verdicts = {j.record.source_id: j.verdict for j in tool.last_result.judgements}
        assert verdicts["u3"] is Verdict.FUTURE
        assert verdicts["u4"] is Verdict.UNDATED
        assert verdicts["u5"] is Verdict.UNPARSEABLE

    def test_decorator_form(self, guard: TemporalGuard, audit: AuditLog) -> None:
        @guarded_tool(guard, WEB_ADAPTER, audit=audit)
        def search(query: str) -> list[dict]:
            """Search things."""
            return hits()

        assert [r.source_id for r in search("q")] == ["u1"]
        assert audit.filtered_count == 4

    def test_wrapper_keeps_name_doc_and_signature(self, guard: TemporalGuard) -> None:
        @guarded_tool(guard, WEB_ADAPTER)
        def search(query: str, limit: int = 5) -> list[dict]:
            """Search things."""
            return hits()

        assert search.__name__ == "search"
        assert search.__doc__ == "Search things."
        assert list(inspect.signature(search).parameters) == ["query", "limit"]

    def test_arguments_are_recorded_by_name(self, guard: TemporalGuard) -> None:
        @guarded_tool(guard, WEB_ADAPTER)
        def search(query: str, limit: int = 5) -> list[dict]:
            return hits()

        search("pricing")
        assert search.calls[0].arguments == {"query": "pricing", "limit": 5}

    def test_unserializable_arguments_are_stringified(self, guard: TemporalGuard) -> None:
        @guarded_tool(guard, WEB_ADAPTER)
        def search(query: object) -> list[dict]:
            return hits()

        search(object())
        assert isinstance(search.calls[0].arguments["query"], str)

    def test_tool_exceptions_propagate_and_are_not_logged(
        self, guard: TemporalGuard, audit: AuditLog
    ) -> None:
        @guarded_tool(guard, WEB_ADAPTER, audit=audit)
        def broken(query: str) -> list[dict]:
            raise RuntimeError("upstream is down")

        with pytest.raises(RuntimeError, match="upstream is down"):
            broken("q")
        assert audit.calls == []

    def test_render_hook_controls_what_the_agent_gets(self, guard: TemporalGuard) -> None:
        tool = guard_tool(
            hits,
            guard,
            WEB_ADAPTER,
            render=lambda result: [{"id": r.source_id} for r in result.kept],
        )
        assert tool() == [{"id": "u1"}]

    def test_render_gets_the_whole_result_not_just_the_survivors(
        self, guard: TemporalGuard
    ) -> None:
        # render is the audit-side hook, so it can see what was dropped. What
        # the agent gets is whatever render returns.
        tool = guard_tool(
            hits,
            guard,
            WEB_ADAPTER,
            render=lambda result: {
                "kept": [r.source_id for r in result.kept],
                "withheld": result.filtered_count,
            },
        )
        assert tool() == {"kept": ["u1"], "withheld": 4}

    def test_warn_policy_lets_content_through_but_still_counts_it(self) -> None:
        tool = guard_tool(hits, TemporalGuard(AS_OF, policy=GuardPolicy.WARN), WEB_ADAPTER)
        blob = " ".join(r.content for r in tool())
        assert "FUTURE-TOKEN" in blob
        assert tool.filtered_count == 0
        assert tool.last_result.violation_count == 4

    def test_a_tool_returning_records_needs_no_adapter(self, guard: TemporalGuard) -> None:
        def fetch() -> list[EvidenceRecord]:
            return [
                EvidenceRecord.from_source("old", "a", published_at=BEFORE),
                EvidenceRecord.from_source("new", "b", published_at=AFTER),
            ]

        assert [r.source_id for r in guard_tool(fetch, guard)()] == ["a"]

    def test_repr_names_the_tool_and_the_cutoff(self, guard: TemporalGuard) -> None:
        assert "hits" in repr(guard_tool(hits, guard, WEB_ADAPTER))
        assert "2023-06-01" in repr(guard_tool(hits, guard, WEB_ADAPTER))

    def test_name_override(self, guard: TemporalGuard) -> None:
        tool = guard_tool(hits, guard, WEB_ADAPTER, name="web_search")
        tool()
        assert tool.calls[0].tool == "web_search"

    def test_counts_accumulate_across_calls(self, guard: TemporalGuard) -> None:
        tool = guard_tool(hits, guard, WEB_ADAPTER)
        tool()
        tool()
        assert tool.kept_count == 2
        assert tool.filtered_count == 8

    def test_last_result_is_none_before_any_call(self, guard: TemporalGuard) -> None:
        assert guard_tool(hits, guard, WEB_ADAPTER).last_result is None

    def test_wrapping_a_class_method(self, guard: TemporalGuard) -> None:
        class Store:
            def search(self, query: str) -> list[dict]:
                return hits()

        tool = guard_tool(Store().search, guard, WEB_ADAPTER)
        assert [r.source_id for r in tool("q")] == ["u1"]


class TestAuditLog:
    def test_totals_across_several_tools(self, guard: TemporalGuard, audit: AuditLog) -> None:
        search = guard_tool(hits, guard, WEB_ADAPTER, name="search", audit=audit)
        store = guard_tool(hits, guard, WEB_ADAPTER, name="store", audit=audit)
        search()
        store()
        store()
        assert audit.call_count == 3
        assert audit.total == 15
        assert audit.kept_count == 3
        assert audit.filtered_count == 12

    def test_each_tool_sees_only_its_own_calls(self, guard: TemporalGuard, audit: AuditLog) -> None:
        search = guard_tool(hits, guard, WEB_ADAPTER, name="search", audit=audit)
        store = guard_tool(hits, guard, WEB_ADAPTER, name="store", audit=audit)
        search()
        store()
        store()
        assert len(search.calls) == 1
        assert len(store.calls) == 2

    def test_by_tool_breakdown(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, name="search", audit=audit)()
        guard_tool(hits, guard, WEB_ADAPTER, name="store", audit=audit)()
        assert audit.by_tool() == {
            "search": {"calls": 1, "total": 5, "kept": 1, "filtered": 4},
            "store": {"calls": 1, "total": 5, "kept": 1, "filtered": 4},
        }

    def test_verdict_counts_are_summed(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, audit=audit)()
        assert audit.counts == {
            "allowed": 1,
            "future": 2,
            "undated": 1,
            "unparseable": 1,
            "revised": 0,
        }

    def test_empty_log(self, audit: AuditLog) -> None:
        assert audit.call_count == 0
        assert audit.filtered_count == 0
        assert audit.counts == {v.value: 0 for v in Verdict}
        assert "0 guarded call(s)" in audit.summary()

    def test_clear_resets(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, audit=audit)()
        audit.clear()
        assert audit.call_count == 0

    def test_summary_reports_the_filtered_count(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, audit=audit)()
        summary = audit.summary()
        assert "kept 1/5" in summary
        assert "filtered 4" in summary

    def test_log_serializes_to_json(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, audit=audit)()
        blob = audit.model_dump_json()
        assert "u1" in blob
        assert isinstance(ToolCall.model_validate(audit.calls[0].model_dump()), ToolCall)


async def async_hits() -> list[dict]:
    """The same raw output as `hits`, behind an await."""
    return hits()


class TestAsyncTools:
    """Wrapping an `async def` has to behave exactly like wrapping its sync twin.

    This is the shape every mainstream agent framework hands you, so getting it
    wrong means ChronoGuard can't guard anything real.
    """

    def test_it_is_recognised_as_async(self, guard: TemporalGuard) -> None:
        assert guard_tool(async_hits, guard, WEB_ADAPTER).is_async is True
        assert guard_tool(hits, guard, WEB_ADAPTER).is_async is False

    def test_it_filters_the_awaited_result(self, guard: TemporalGuard) -> None:
        tool = guard_tool(async_hits, guard, WEB_ADAPTER)
        kept = asyncio.run(tool())
        assert [r.source_id for r in kept] == ["u1"]

    def test_it_keeps_exactly_what_the_sync_twin_keeps(self, guard: TemporalGuard) -> None:
        sync_kept = guard_tool(hits, guard, WEB_ADAPTER)()
        async_kept = asyncio.run(guard_tool(async_hits, guard, WEB_ADAPTER)())
        assert [r.source_id for r in async_kept] == [r.source_id for r in sync_kept]

    def test_the_future_token_never_survives(self, guard: TemporalGuard) -> None:
        kept = asyncio.run(guard_tool(async_hits, guard, WEB_ADAPTER)())
        assert all("FUTURE-TOKEN" not in r.content for r in kept)

    def test_calling_it_returns_something_awaitable(self, guard: TemporalGuard) -> None:
        tool = guard_tool(async_hits, guard, WEB_ADAPTER)
        pending = tool()
        assert inspect.isawaitable(pending)
        asyncio.run(pending)

    def test_nothing_is_audited_until_it_is_awaited(self, guard: TemporalGuard, audit: AuditLog) -> None:
        # Filtering can't happen before the tool has produced results, so the
        # log must stay empty until the coroutine actually runs.
        tool = guard_tool(async_hits, guard, WEB_ADAPTER, audit=audit)
        pending = tool()
        assert audit.call_count == 0
        asyncio.run(pending)
        assert audit.call_count == 1

    def test_it_files_the_same_audit_entry(self, guard: TemporalGuard, audit: AuditLog) -> None:
        asyncio.run(guard_tool(async_hits, guard, WEB_ADAPTER, audit=audit)())
        assert audit.counts == {
            "allowed": 1,
            "future": 2,
            "undated": 1,
            "unparseable": 1,
            "revised": 0,
        }

    def test_sync_and_async_tools_share_one_log(self, guard: TemporalGuard, audit: AuditLog) -> None:
        guard_tool(hits, guard, WEB_ADAPTER, audit=audit, name="sync")()
        asyncio.run(guard_tool(async_hits, guard, WEB_ADAPTER, audit=audit, name="async")())
        assert audit.call_count == 2
        assert set(audit.by_tool()) == {"sync", "async"}

    def test_arguments_are_recorded(self, guard: TemporalGuard, audit: AuditLog) -> None:
        async def search(query: str, limit: int = 5) -> list[dict]:
            return hits()

        asyncio.run(guard_tool(search, guard, WEB_ADAPTER, audit=audit)("meridian"))
        assert audit.calls[0].arguments == {"query": "meridian", "limit": 5}

    def test_the_render_hook_still_applies(self, guard: TemporalGuard) -> None:
        tool = guard_tool(async_hits, guard, WEB_ADAPTER, render=lambda r: r.kept_count)
        assert asyncio.run(tool()) == 1

    def test_metadata_is_copied_for_schema_building(self, guard: TemporalGuard) -> None:
        async def search(query: str) -> list[dict]:
            """Search the archive."""
            return hits()

        tool = guard_tool(search, guard, WEB_ADAPTER)
        assert tool.__name__ == "search"
        assert tool.__doc__ == "Search the archive."
        assert list(inspect.signature(tool).parameters) == ["query"]

    def test_repr_says_it_is_async(self, guard: TemporalGuard) -> None:
        assert "async" in repr(guard_tool(async_hits, guard, WEB_ADAPTER))
        assert "async" not in repr(guard_tool(hits, guard, WEB_ADAPTER))

    def test_an_object_with_an_async_call_is_handled(self, guard: TemporalGuard) -> None:
        class Retriever:
            async def __call__(self, query: str) -> list[dict]:
                return hits()

        tool = guard_tool(Retriever(), guard, WEB_ADAPTER, name="retriever")
        assert tool.is_async is True
        assert [r.source_id for r in asyncio.run(tool("x"))] == ["u1"]

    def test_an_object_with_a_sync_call_is_not_treated_as_async(self, guard: TemporalGuard) -> None:
        class Retriever:
            def __call__(self, query: str) -> list[dict]:
                return hits()

        assert guard_tool(Retriever(), guard, WEB_ADAPTER, name="retriever").is_async is False

    def test_the_tool_raising_propagates(self, guard: TemporalGuard, audit: AuditLog) -> None:
        async def broken() -> list[dict]:
            raise RuntimeError("upstream is down")

        tool = guard_tool(broken, guard, WEB_ADAPTER, audit=audit)
        with pytest.raises(RuntimeError, match="upstream is down"):
            asyncio.run(tool())
        assert audit.call_count == 0

    def test_concurrent_calls_all_land_in_the_log(self, guard: TemporalGuard, audit: AuditLog) -> None:
        tool = guard_tool(async_hits, guard, WEB_ADAPTER, audit=audit)

        async def run_all() -> None:
            await asyncio.gather(*(tool() for _ in range(4)))

        asyncio.run(run_all())
        assert audit.call_count == 4
        assert audit.kept_count == 4


class TestMappingAdapterRevisions:
    def test_the_revision_field_is_off_until_named(self) -> None:
        row = [{"url": "u", "content": "c", "date": BEFORE, "modified": AFTER}]
        assert MappingAdapter(source_key="url").to_records(row)[0].updated_at is None

    def test_naming_it_maps_it(self) -> None:
        row = [{"url": "u", "content": "c", "date": BEFORE, "modified": AFTER}]
        adapter = MappingAdapter(source_key="url", published_key="date", updated_key="modified")
        assert adapter.to_records(row)[0].updated_at == datetime(2023, 9, 15, tzinfo=UTC)

    def test_an_unnamed_revision_field_lands_in_metadata_instead(self) -> None:
        row = [{"url": "u", "content": "c", "date": BEFORE, "modified": AFTER}]
        record = MappingAdapter(source_key="url", published_key="date").to_records(row)[0]
        assert record.metadata["modified"] == AFTER

    def test_a_named_revision_field_is_consumed_not_duplicated(self) -> None:
        row = [{"url": "u", "content": "c", "date": BEFORE, "modified": AFTER}]
        adapter = MappingAdapter(source_key="url", published_key="date", updated_key="modified")
        assert "modified" not in adapter.to_records(row)[0].metadata

    def test_several_candidate_keys_take_the_first_hit(self) -> None:
        row = [{"url": "u", "content": "c", "date": BEFORE, "last_modified": AFTER}]
        adapter = MappingAdapter(
            source_key="url", published_key="date", updated_key=("modified", "last_modified")
        )
        assert adapter.to_records(row)[0].updated_at == datetime(2023, 9, 15, tzinfo=UTC)

    def test_a_revised_row_is_dropped_end_to_end(self, guard: TemporalGuard) -> None:
        def wiki() -> list[dict]:
            return [
                {"url": "stable", "content": "c", "date": BEFORE},
                {"url": "edited", "content": "REVISED-TOKEN", "date": BEFORE, "modified": AFTER},
            ]

        adapter = MappingAdapter(source_key="url", published_key="date", updated_key="modified")
        kept = guard_tool(wiki, guard, adapter)()
        assert [r.source_id for r in kept] == ["stable"]
