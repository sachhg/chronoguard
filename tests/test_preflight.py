"""Tests for corpus preflight.

Everything here is offline and model-free, which is the whole point of the
module: answering "will this corpus work" should not cost a model call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chronoguard.guard import TemporalGuard
from chronoguard.interception import MappingAdapter
from chronoguard.preflight import (
    CorpusReport,
    Finding,
    inspect_corpus,
    load_rows,
    sniff_timestamp_fields,
)

AS_OF = "2023-06-01T00:00:00Z"
BEFORE = "2023-01-15T00:00:00Z"
AFTER = "2023-09-15T00:00:00Z"


def row(source_id: str, published: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": source_id, "content": f"body of {source_id}"}
    if published is not None:
        out["published_at"] = published
    out.update(extra)
    return out


def straddling() -> list[dict[str, Any]]:
    """A corpus the guard has real work to do on."""
    return [row("a", BEFORE), row("b", BEFORE), row("c", AFTER)]


def write(tmp_path: Path, name: str, payload: Any) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadRows:
    def test_a_plain_json_array(self, tmp_path: Path) -> None:
        path = write(tmp_path, "c.json", straddling())
        assert len(load_rows(path)) == 3

    def test_jsonl_by_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "c.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in straddling()), encoding="utf-8")
        assert len(load_rows(path)) == 3

    def test_jsonl_by_shape_without_the_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text("\n".join(json.dumps(r) for r in straddling()), encoding="utf-8")
        assert len(load_rows(path)) == 3

    def test_blank_lines_in_jsonl_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "c.jsonl"
        path.write_text(json.dumps(row("a", BEFORE)) + "\n\n" + json.dumps(row("b", BEFORE)))
        assert len(load_rows(path)) == 2

    def test_a_wrapper_object_with_one_list_is_found(self, tmp_path: Path) -> None:
        path = write(tmp_path, "c.json", {"status": "ok", "items": straddling()})
        assert len(load_rows(path)) == 3

    def test_a_named_results_key_wins(self, tmp_path: Path) -> None:
        payload = {"matches": straddling(), "related": [row("z", BEFORE)]}
        assert len(load_rows(write(tmp_path, "c.json", payload), results_key="matches")) == 3

    def test_an_ambiguous_wrapper_asks_for_a_results_key(self, tmp_path: Path) -> None:
        payload = {"matches": straddling(), "related": [row("z", BEFORE)]}
        with pytest.raises(ValueError, match="--results-key"):
            load_rows(write(tmp_path, "c.json", payload))

    def test_a_missing_results_key_lists_what_is_there(self, tmp_path: Path) -> None:
        path = write(tmp_path, "c.json", {"items": straddling()})
        with pytest.raises(ValueError, match="Keys present: items"):
            load_rows(path, results_key="matches")

    def test_a_missing_file_is_a_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot read"):
            load_rows(tmp_path / "nope.json")

    def test_an_empty_file_says_so(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="is empty"):
            load_rows(path)

    def test_broken_json_names_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            load_rows(path)

    def test_broken_jsonl_names_the_line(self, tmp_path: Path) -> None:
        path = tmp_path / "c.jsonl"
        path.write_text('{"a": 1}\n{broken\n', encoding="utf-8")
        with pytest.raises(ValueError, match="line 2"):
            load_rows(path)

    def test_a_list_of_scalars_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="expected an object"):
            load_rows(write(tmp_path, "c.json", [1, 2, 3]))

    def test_a_bare_scalar_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="expected a list"):
            load_rows(write(tmp_path, "c.json", 42))


class TestSniffing:
    def test_it_finds_a_timestamp_column(self) -> None:
        fields = sniff_timestamp_fields(straddling())
        assert [f.name for f in fields] == ["published_at"]

    def test_it_ignores_a_text_column(self) -> None:
        assert "content" not in {f.name for f in sniff_timestamp_fields(straddling())}

    def test_a_single_stray_date_is_not_a_column(self) -> None:
        rows = [row("a", BEFORE), {"id": "b", "content": "we shipped on 2023-01-01"}]
        assert "content" not in {f.name for f in sniff_timestamp_fields(rows)}

    def test_naive_timestamps_are_flagged_as_needing_a_timezone(self) -> None:
        rows = [{"id": "a", "created": "2023-01-15T00:00:00"} for _ in range(3)]
        field = sniff_timestamp_fields(rows)[0]
        assert field.name == "created"
        assert field.needs_timezone is True

    def test_aware_timestamps_are_not_flagged(self) -> None:
        assert sniff_timestamp_fields(straddling())[0].needs_timezone is False

    def test_empty_values_do_not_count_against_a_column(self) -> None:
        rows = [row("a", BEFORE), {"id": "b", "content": "x", "published_at": ""}]
        assert sniff_timestamp_fields(rows)[0].parsed == 1

    def test_revision_names_are_recognised(self) -> None:
        rows = [{"id": "a", "last_modified": BEFORE} for _ in range(3)]
        assert sniff_timestamp_fields(rows)[0].looks_like_a_revision is True

    def test_a_publication_name_is_not_a_revision(self) -> None:
        assert sniff_timestamp_fields(straddling())[0].looks_like_a_revision is False

    def test_no_rows_means_no_fields(self) -> None:
        assert sniff_timestamp_fields([]) == []


class TestCounting:
    def test_it_reports_what_the_guard_would_do(self) -> None:
        report = inspect_corpus(straddling(), AS_OF)
        assert report.rows == 3
        assert report.kept == 2
        assert report.dropped == 1

    def test_the_drop_rate(self) -> None:
        assert inspect_corpus(straddling(), AS_OF).drop_rate == pytest.approx(1 / 3)

    def test_verdict_counts_come_through(self) -> None:
        counts = inspect_corpus(straddling(), AS_OF).counts
        assert counts["allowed"] == 2
        assert counts["future"] == 1

    def test_a_straddling_corpus_is_usable(self) -> None:
        assert inspect_corpus(straddling(), AS_OF).usable is True

    def test_an_all_past_corpus_is_not_usable(self) -> None:
        assert inspect_corpus([row("a", BEFORE)], AS_OF).usable is False

    def test_an_all_future_corpus_is_not_usable(self) -> None:
        assert inspect_corpus([row("a", AFTER)], AS_OF).usable is False

    def test_it_reports_which_fields_it_read(self) -> None:
        mapped = inspect_corpus(straddling(), AS_OF).mapped
        assert mapped["published"] == "published_at|published|date"
        assert "updated" not in mapped

    def test_a_mapped_revision_field_shows_up(self) -> None:
        adapter = MappingAdapter(source_key="id", updated_key="modified")
        report = inspect_corpus(straddling(), AS_OF, adapter=adapter)
        assert report.mapped["updated"] == "modified"

    def test_a_revised_record_is_counted(self) -> None:
        rows = [row("a", BEFORE), row("b", BEFORE, modified=AFTER)]
        adapter = MappingAdapter(source_key="id", updated_key="modified")
        report = inspect_corpus(rows, AS_OF, adapter=adapter)
        assert report.counts["revised"] == 1
        assert report.kept == 1

    def test_an_explicit_guard_is_used(self) -> None:
        rows = [row("a", BEFORE), row("b", BEFORE, modified=AFTER)]
        adapter = MappingAdapter(source_key="id", updated_key="modified")
        lenient = TemporalGuard(AS_OF, revisions="ignore")
        assert inspect_corpus(rows, AS_OF, adapter=adapter, guard=lenient).kept == 2

    def test_unparseable_revision_stamps_are_counted(self) -> None:
        rows = [row("a", BEFORE, modified="last tuesday")]
        adapter = MappingAdapter(source_key="id", updated_key="modified")
        assert inspect_corpus(rows, AS_OF, adapter=adapter).unparseable_revisions == 1


class TestFindings:
    def messages(self, report: CorpusReport) -> str:
        return " ".join(f"{f.message} {f.fix}" for f in report.findings)

    def test_a_healthy_corpus_raises_no_errors(self) -> None:
        assert inspect_corpus(straddling(), AS_OF).errors == []

    def test_dropping_everything_is_an_error(self) -> None:
        report = inspect_corpus([row("a", AFTER), row("b", AFTER)], AS_OF)
        assert [f.severity for f in report.errors] == ["error"]
        assert "no evidence at all" in self.messages(report)

    def test_dropping_nothing_is_an_error(self) -> None:
        # The failure mode that looks like success: the guard had no job to do,
        # so a clean run proves nothing.
        report = inspect_corpus([row("a", BEFORE), row("b", BEFORE)], AS_OF)
        assert report.errors
        assert "drops nothing" in self.messages(report)

    def test_an_empty_corpus_is_an_error(self) -> None:
        report = inspect_corpus([], AS_OF)
        assert report.errors
        assert "empty" in self.messages(report)

    def test_an_unmapped_revision_field_is_a_warning(self) -> None:
        rows = [row("a", BEFORE, last_modified=AFTER), row("b", BEFORE, last_modified=AFTER),
                row("c", AFTER, last_modified=AFTER)]
        report = inspect_corpus(rows, AS_OF)
        warnings = [f for f in report.findings if f.severity == "warning"]
        assert any("last_modified" in f.message for f in warnings)
        assert "--updated-key last_modified" in self.messages(report)

    def test_a_mapped_revision_field_stops_warning(self) -> None:
        rows = [row("a", BEFORE, last_modified=AFTER), row("b", BEFORE, last_modified=BEFORE),
                row("c", AFTER, last_modified=AFTER)]
        adapter = MappingAdapter(source_key="id", updated_key="last_modified")
        report = inspect_corpus(rows, AS_OF, adapter=adapter)
        assert "last_modified" not in self.messages(report)

    def test_a_mostly_undated_corpus_blames_the_mapping(self) -> None:
        rows = [{"id": f"d{i}", "content": "x", "issued": BEFORE} for i in range(4)]
        rows.append(row("late", AFTER))
        report = inspect_corpus(rows, AS_OF)
        assert "--published-key is probably wrong" in self.messages(report)
        assert "issued" in self.messages(report)

    def test_a_mostly_dated_corpus_does_not_blame_the_mapping(self) -> None:
        rows = [row(f"d{i}", BEFORE) for i in range(8)] + [row("x"), row("y", AFTER)]
        report = inspect_corpus(rows, AS_OF)
        assert "probably wrong" not in self.messages(report)

    def test_naive_timestamps_get_the_assume_tz_advice(self) -> None:
        rows = [{"id": f"d{i}", "content": "x", "published_at": "2023-01-15T00:00:00"}
                for i in range(4)]
        report = inspect_corpus(rows, AS_OF)
        assert "assume_tz" in self.messages(report)

    def test_a_revision_field_is_never_offered_as_the_publication_fix(self) -> None:
        rows = [{"id": f"d{i}", "content": "x", "last_modified": BEFORE} for i in range(4)]
        report = inspect_corpus(rows, AS_OF)
        fix = " ".join(
            f.fix for f in report.findings if "no usable publication date" in f.message
        )
        assert "last_modified" not in fix

    def test_allow_undated_is_called_out_as_dangerous(self) -> None:
        rows = [row("a", BEFORE), row("b"), row("c", AFTER)]
        guard = TemporalGuard(AS_OF, allow_undated=True)
        report = inspect_corpus(rows, AS_OF, guard=guard)
        assert "allow_undated is on" in self.messages(report)

    def test_unparseable_revisions_are_mentioned(self) -> None:
        rows = [row("a", BEFORE, modified="who knows"), row("b", AFTER)]
        adapter = MappingAdapter(source_key="id", updated_key="modified")
        report = inspect_corpus(rows, AS_OF, adapter=adapter)
        assert "does not parse" in self.messages(report)


class TestRendering:
    def test_the_text_report_names_the_source(self) -> None:
        text = inspect_corpus(straddling(), AS_OF, source="corpus.json").render()
        assert "corpus.json" in text

    def test_it_shows_the_counts(self) -> None:
        text = inspect_corpus(straddling(), AS_OF).render()
        assert "rows     3" in text
        assert "kept     2" in text
        assert "allowed=2" in text

    def test_findings_appear(self) -> None:
        text = inspect_corpus([row("a", BEFORE)], AS_OF).render()
        assert "ERROR" in text

    def test_a_finding_renders_its_fix(self) -> None:
        rendered = Finding(severity="warning", message="m", fix="do this").render()
        assert "m" in rendered and "do this" in rendered

    def test_a_finding_without_a_fix_is_one_line(self) -> None:
        assert "\n" not in Finding(severity="note", message="m").render()


class TestSummary:
    def test_it_is_json_serialisable(self) -> None:
        json.dumps(inspect_corpus(straddling(), AS_OF).summary())

    def test_it_carries_a_schema_version(self) -> None:
        assert inspect_corpus(straddling(), AS_OF).summary()["schema_version"] == 1

    def test_the_top_level_keys_are_stable(self) -> None:
        # Consumers diff these across runs, so adding is fine and removing isn't.
        summary = inspect_corpus(straddling(), AS_OF).summary()
        assert {
            "schema_version", "source", "as_of", "rows", "kept", "dropped",
            "drop_rate", "usable", "verdicts", "unparseable_revisions",
            "unmapped_timestamp_fields", "naive_timestamp_fields", "findings", "mapped",
        } <= set(summary)

    def test_findings_survive_the_round_trip(self) -> None:
        summary = inspect_corpus([row("a", BEFORE)], AS_OF).summary()
        assert any(f["severity"] == "error" for f in summary["findings"])

    def test_unmapped_fields_are_listed(self) -> None:
        rows = [row("a", BEFORE, last_modified=AFTER) for _ in range(3)]
        names = [
            f["name"] for f in inspect_corpus(rows, AS_OF).summary()["unmapped_timestamp_fields"]
        ]
        assert "last_modified" in names

    def test_naive_fields_are_listed(self) -> None:
        rows = [{"id": f"d{i}", "content": "x", "published_at": "2023-01-15T00:00:00"}
                for i in range(3)]
        assert inspect_corpus(rows, AS_OF).summary()["naive_timestamp_fields"] == ["published_at"]
