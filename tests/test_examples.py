"""The shipped example has to keep working, offline.

The model call needs Ollama, but everything up to it does not: the corpus, the
adapter, the guard wiring, and the leakage assertions all run without a network.
Those are the parts that break when the library changes underneath the example.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chronoguard import AuditLog, TemporalGuard, guard_tool
from chronoguard.guard import Verdict

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "policy_change"
sys.path.insert(0, str(EXAMPLE))

from archive import AS_OF, CANARIES, CouncilArchive, load_corpus  # noqa: E402


@pytest.fixture
def archive() -> CouncilArchive:
    return CouncilArchive()


@pytest.fixture
def guarded(archive: CouncilArchive):
    return guard_tool(
        archive.search, TemporalGuard(AS_OF), archive.adapter, name="council_archive", audit=AuditLog()
    )


class TestCorpus:
    def test_it_loads(self) -> None:
        assert len(load_corpus()) >= 8

    def test_refs_are_unique(self) -> None:
        refs = [r["ref"] for r in load_corpus()]
        assert len(refs) == len(set(refs))

    def test_every_verdict_is_represented(self, archive: CouncilArchive) -> None:
        guard = TemporalGuard(AS_OF)
        verdicts = {guard.judge(r).verdict for r in archive.adapter.to_records({"items": archive.rows})}
        assert verdicts == set(Verdict), f"corpus lost coverage of {set(Verdict) - verdicts}"

    def test_canaries_only_appear_in_records_the_guard_rejects(self, archive: CouncilArchive) -> None:
        guard = TemporalGuard(AS_OF)
        for record in archive.adapter.to_records({"items": archive.rows}):
            if guard.allows(record):
                assert [c for c in CANARIES if c in record.content] == [], record.source_id

    def test_undated_records_carry_future_facts_on_purpose(self, archive: CouncilArchive) -> None:
        guard = TemporalGuard(AS_OF)
        undated = [
            r
            for r in archive.adapter.to_records({"items": archive.rows})
            if guard.judge(r).verdict in (Verdict.UNDATED, Verdict.UNPARSEABLE)
        ]
        assert any(c in r.content for r in undated for c in CANARIES)


class TestAdapter:
    def test_it_maps_the_archive_shape(self, archive: CouncilArchive) -> None:
        records = archive.adapter.to_records(archive.search("consultation", limit=1))
        assert records[0].source_id.startswith("ASH-")
        assert records[0].metadata["committee"]

    def test_the_results_key_digs_into_the_wrapper(self, archive: CouncilArchive) -> None:
        raw = archive.search("levy")
        assert set(raw) == {"status", "items"}
        assert archive.adapter.to_records(raw)


class TestGuarding:
    def test_the_unguarded_archive_leaks(self, archive: CouncilArchive) -> None:
        # Without this the test below could pass because search returned nothing.
        assert [c for c in CANARIES if c in str(archive.search("levy approved charge", limit=99))]

    def test_the_guarded_archive_leaks_nothing(self, guarded) -> None:
        queries = ["levy approved charge", "daily charge", "October start date", "Rowe", "traffic"]
        for query in queries:
            text = " ".join(r.content for r in guarded(query, limit=99))
            assert [c for c in CANARIES if c in text] == [], query

    def test_every_surviving_record_predates_the_cutoff(self, guarded) -> None:
        guard = TemporalGuard(AS_OF)
        for record in guarded("levy", limit=99):
            assert record.published_at is not None
            assert record.published_at < guard.as_of

    def test_the_boundary_record_is_dropped(self, guarded) -> None:
        assert "ASH-2024-041" not in [r.source_id for r in guarded("council vote scheduled", limit=99)]

    def test_filtered_counts_are_reported(self, guarded) -> None:
        guarded("levy approved charge", limit=99)
        assert guarded.filtered_count > 0
        assert guarded.last_result.counts["future"] > 0


class TestScript:
    def test_the_runner_imports_and_wires_tools(self) -> None:
        import run  # noqa: PLC0415

        tools = run.build_tools(TemporalGuard(AS_OF), AuditLog())
        assert set(tools) == {"council_archive"}
        assert tools["council_archive"].guard.as_of.isoformat().startswith("2024-03-01")

    def test_the_task_asks_the_question_the_corpus_answers(self) -> None:
        import run  # noqa: PLC0415

        assert "levy" in run.TASK.lower()
