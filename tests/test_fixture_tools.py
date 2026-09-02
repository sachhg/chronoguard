"""Tests for the fixture tools and the corpora behind them.

The headline assertion is the one the whole layer exists for: nothing published
at or after the as-of instant reaches the agent, no matter what it searches for.
"""

from __future__ import annotations

import pytest

from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import GuardPolicy, TemporalGuard, Verdict
from chronoguard.interception import AuditLog
from chronoguard.fixtures import (
    FIXTURE_AS_OF,
    POST_AS_OF_CANARIES,
    FakeDocumentStore,
    FakeWebSearch,
    build_fixture_toolset,
    load_corpus,
)

# Queries picked to drag the post-as-of documents into the raw results.
ADVERSARIAL_QUERIES = [
    "meridian price",
    "meridian launch date",
    "october ship date",
    "ferrous labs acquisition",
    "how many seats sold",
    "$4,900 per seat",
    "halden systems",
    "meridian",
    "corbel migration",
    "outage",
]


@pytest.fixture
def guard() -> TemporalGuard:
    return TemporalGuard(FIXTURE_AS_OF)


def canaries_in(text: str) -> list[str]:
    return [c for c in POST_AS_OF_CANARIES if c in text]


def blob(records: list[EvidenceRecord]) -> str:
    return " ".join(f"{r.source_id} {r.content} {r.metadata}" for r in records)


class TestCorpusDesign:
    """The corpora need teeth. These fail if someone waters them down."""

    def test_both_corpora_load(self) -> None:
        assert len(load_corpus("web_corpus.json")) >= 10
        assert len(load_corpus("doc_store.json")) >= 8

    @pytest.mark.parametrize("tool", [FakeWebSearch(), FakeDocumentStore()])
    def test_every_verdict_is_represented(self, tool: object, guard: TemporalGuard) -> None:
        records = tool.adapter.to_records(tool.rows)
        verdicts = {guard.judge(r).verdict for r in records}
        assert verdicts == set(Verdict), f"corpus lost coverage of {set(Verdict) - verdicts}"

    def test_a_document_sits_exactly_on_the_boundary(self, guard: TemporalGuard) -> None:
        web = FakeWebSearch()
        exact = [
            r
            for r in web.adapter.to_records(web.rows)
            if r.published_at == guard.as_of
        ]
        assert len(exact) == 1, "the boundary document is what makes the exclusive rule testable"

    def test_canaries_only_live_in_documents_the_guard_rejects(self, guard: TemporalGuard) -> None:
        for tool in (FakeWebSearch(), FakeDocumentStore()):
            for record in tool.adapter.to_records(tool.rows):
                if guard.allows(record):
                    assert canaries_in(record.content) == [], (
                        f"{record.source_id} is allowed but carries a post-as-of fact"
                    )

    def test_some_rejected_documents_carry_canaries_without_a_usable_date(
        self, guard: TemporalGuard
    ) -> None:
        # Undated content holding future facts is the reason the conservative
        # default matters. If these vanish, allow_undated stops being dangerous
        # and the default stops being interesting.
        undated_canaries = 0
        for tool in (FakeWebSearch(), FakeDocumentStore()):
            for record in tool.adapter.to_records(tool.rows):
                verdict = guard.judge(record).verdict
                if verdict in (Verdict.UNDATED, Verdict.UNPARSEABLE) and canaries_in(record.content):
                    undated_canaries += 1
        assert undated_canaries >= 2

    def test_pre_as_of_documents_contain_the_plausible_wrong_answer(self) -> None:
        # An agent that reads the evidence should guess "below $3,000" and
        # "summer". An agent leaking from weights says "$4,900" and "October 14".
        text = " ".join(f'{r["title"]} {r["snippet"]}' for r in load_corpus("web_corpus.json"))
        assert "$3,000" in text
        assert "summer" in text


class TestRawToolsLeak:
    """Without the guard these tools hand back the future. Otherwise the tests
    below would pass for the wrong reason."""

    def test_raw_web_search_returns_post_as_of_hits(self) -> None:
        text = str(FakeWebSearch().search("meridian price launch", limit=None))
        assert canaries_in(text), "raw search stopped leaking, the guard test is now vacuous"

    def test_raw_document_store_returns_post_as_of_matches(self) -> None:
        text = str(FakeDocumentStore().retrieve("meridian pricing launch", k=99))
        assert canaries_in(text)


class TestGuardedTools:
    def test_no_canary_survives_any_query(self, guard: TemporalGuard) -> None:
        tools = build_fixture_toolset(guard)
        leaked: list[tuple[str, str]] = []
        for query in ADVERSARIAL_QUERIES:
            web = blob(tools["web_search"](query, limit=99))
            store = blob(tools["document_store"](query, k=99))
            leaked += [(query, c) for c in canaries_in(web) + canaries_in(store)]
        assert leaked == []

    def test_every_kept_record_predates_the_cutoff(self, guard: TemporalGuard) -> None:
        tools = build_fixture_toolset(guard)
        for query in ADVERSARIAL_QUERIES:
            for record in tools["web_search"](query, limit=99) + tools["document_store"](query, k=99):
                assert record.published_at is not None
                assert record.published_at < guard.as_of

    def test_the_boundary_document_is_dropped(self, guard: TemporalGuard) -> None:
        kept = build_fixture_toolset(guard)["web_search"]("halden launch event", limit=99)
        assert "launch-event-scheduled" not in blob(kept)

    def test_filtered_counts_are_available_for_reporting(self, guard: TemporalGuard) -> None:
        audit = AuditLog()
        tools = build_fixture_toolset(guard, audit)
        tools["web_search"]("meridian", limit=99)
        tools["document_store"]("meridian", k=99)

        assert audit.call_count == 2
        assert audit.filtered_count > 0
        assert audit.kept_count + audit.filtered_count == audit.total
        assert audit.counts["future"] > 0
        assert set(audit.by_tool()) == {"web_search", "document_store"}
        assert "filtered" in audit.summary()

    def test_both_tools_share_one_audit_log(self, guard: TemporalGuard) -> None:
        audit = AuditLog()
        tools = build_fixture_toolset(guard, audit)
        tools["web_search"]("meridian")
        tools["document_store"]("meridian")
        assert [c.tool for c in audit.calls] == ["web_search", "document_store"]

    def test_toolset_makes_its_own_log_when_none_is_given(self, guard: TemporalGuard) -> None:
        tools = build_fixture_toolset(guard)
        tools["web_search"]("meridian")
        assert tools["web_search"].audit is tools["document_store"].audit
        assert tools["web_search"].audit.call_count == 1

    def test_warn_policy_shows_how_much_would_have_leaked(self) -> None:
        tools = build_fixture_toolset(TemporalGuard(FIXTURE_AS_OF, policy=GuardPolicy.WARN))
        kept = tools["web_search"]("meridian price launch", limit=99)
        assert canaries_in(blob(kept)), "warn is supposed to let it through, flagged"
        assert tools["web_search"].last_result.violation_count > 0
        assert tools["web_search"].filtered_count == 0

    def test_signatures_survive_wrapping(self, guard: TemporalGuard) -> None:
        tools = build_fixture_toolset(guard)
        assert tools["web_search"].__name__ == "web_search"
        assert "Search the web" in (tools["web_search"].__doc__ or "")
        assert "Retrieve up to" in (tools["document_store"].__doc__ or "")


class TestSearchBehaviour:
    def test_results_are_deterministic(self) -> None:
        web = FakeWebSearch()
        assert web.search("meridian pricing") == web.search("meridian pricing")

    def test_limit_is_respected(self) -> None:
        assert len(FakeWebSearch().search("meridian", limit=2)) == 2

    def test_k_is_respected(self) -> None:
        assert len(FakeDocumentStore().retrieve("meridian", k=2)["matches"]) == 2

    def test_no_match_gives_nothing(self) -> None:
        assert FakeWebSearch().search("zzzzz nonexistent") == []
        assert FakeDocumentStore().retrieve("zzzzz nonexistent")["matches"] == []

    def test_guarded_tool_handles_an_empty_result(self, guard: TemporalGuard) -> None:
        tools = build_fixture_toolset(guard)
        assert tools["web_search"]("zzzzz nonexistent") == []
        assert tools["web_search"].last_result.total == 0

    def test_document_store_wraps_results_in_a_dict(self) -> None:
        out = FakeDocumentStore().retrieve("meridian")
        assert set(out) == {"query", "matches"}

    def test_adapters_map_the_two_different_shapes(self) -> None:
        web = FakeWebSearch()
        store = FakeDocumentStore()
        web_record = web.adapter.to_records(web.search("architecture", limit=1))
        store_records = store.adapter.to_records(store.retrieve("architecture", k=1))
        assert store_records[0].source_id == "HAL-ARCH-001"
        assert store_records[0].metadata["author"] == "p.osei"
        assert all(r.source_id.startswith("https://") for r in web_record)

    def test_corpus_can_be_swapped_for_a_custom_one(self) -> None:
        web = FakeWebSearch(rows=[{"url": "u", "title": "custom", "snippet": "", "date": "2020-01-01T00:00:00Z"}])
        assert web.search("custom")[0]["url"] == "u"
