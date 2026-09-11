"""Corpus preflight. What the guard would do, without running a model.

The first question anyone has is "will this even work on my data", and until
now the only way to find out was to boot a model and run a whole scenario. This
answers it offline, in milliseconds, using nothing but Layer 1.

    chronoguard check corpus.json --as-of 2024-03-01T00:00:00Z \\
      --published-key created_utc --source-key doc_id

What it's actually looking for is the two states that make a run worthless, and
which look like success from a distance:

* **Everything dropped.** Usually the wrong field name or the wrong as-of, not a
  corpus that's genuinely all in the future. An agent with no evidence produces
  an answer from its weights alone, which is the opposite of what you wanted.
* **Nothing dropped.** The corpus holds no post-as-of content at all, so the
  guard never had anything to do and a clean run proves nothing. See
  docs/kb/test-that-the-raw-tool-leaks-first.md.

It also sniffs for timestamp fields you didn't map. A `last_modified` column
sitting unmapped in `metadata` is a revision leak waiting to happen (see
docs/kb/revision-dates-are-a-third-channel.md), and a date field that parses
only once you assume a timezone is the difference between a usable corpus and
one the guard rejects wholesale.

Nothing here talks to a model, the network, or Ollama.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from chronoguard.evidence import parse_timestamp
from chronoguard.guard import TemporalGuard, Verdict
from chronoguard.interception import MappingAdapter

__all__ = [
    "CorpusReport",
    "Finding",
    "TimestampField",
    "inspect_corpus",
    "load_rows",
    "sniff_timestamp_fields",
]

#: A field has to parse this often to count as a timestamp column rather than a
#: text field that happens to hold a date in one row.
SNIFF_THRESHOLD = 0.6

#: Names that suggest a revision timestamp. Only used to phrase the advice, the
#: sniffer finds the fields either way.
REVISION_HINTS = ("updated", "modified", "edited", "amended", "revised", "changed")

Severity = Literal["error", "warning", "note"]


class Finding(BaseModel):
    """Something about this corpus the user should know before spending a model call."""

    model_config = ConfigDict(extra="forbid")

    severity: Severity
    message: str
    fix: str = ""

    def render(self) -> str:
        mark = {"error": "ERROR  ", "warning": "WARNING", "note": "note   "}[self.severity]
        out = f"  {mark}  {self.message}"
        if self.fix:
            out += f"\n           {self.fix}"
        return out


class TimestampField(BaseModel):
    """A field that looks like it holds timestamps."""

    model_config = ConfigDict(extra="forbid")

    name: str
    parsed: int
    present: int
    needs_timezone: bool = Field(
        default=False,
        description="Values only parse once a timezone is assumed, so the guard rejects them as-is.",
    )

    @property
    def rate(self) -> float:
        return self.parsed / self.present if self.present else 0.0

    @property
    def looks_like_a_revision(self) -> bool:
        return any(hint in self.name.lower() for hint in REVISION_HINTS)


class CorpusReport(BaseModel):
    """What the guard would do to a corpus at a given as-of."""

    model_config = ConfigDict(extra="forbid")

    source: str
    as_of: AwareDatetime
    rows: int
    counts: dict[str, int]
    unmapped_fields: list[TimestampField] = Field(
        default_factory=list,
        description="Timestamp-looking fields the adapter isn't reading.",
    )
    naive_fields: list[TimestampField] = Field(
        default_factory=list,
        description="Date fields whose values only parse once a timezone is assumed, mapped or not.",
    )
    unparseable_revisions: int = 0
    findings: list[Finding] = Field(default_factory=list)
    mapped: dict[str, Any] = Field(default_factory=dict)

    @property
    def kept(self) -> int:
        return self.counts.get(Verdict.ALLOWED.value, 0)

    @property
    def dropped(self) -> int:
        return self.rows - self.kept

    @property
    def drop_rate(self) -> float:
        return self.dropped / self.rows if self.rows else 0.0

    @property
    def usable(self) -> bool:
        """Whether a run on this corpus could tell you anything.

        False when the guard would drop everything (the agent gets no evidence)
        or nothing (the guard never had a job to do).
        """
        return 0 < self.kept < self.rows

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def summary(self) -> dict[str, Any]:
        """Machine-readable, same shape every time."""
        return {
            "schema_version": 1,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "rows": self.rows,
            "kept": self.kept,
            "dropped": self.dropped,
            "drop_rate": round(self.drop_rate, 4),
            "usable": self.usable,
            "verdicts": self.counts,
            "unparseable_revisions": self.unparseable_revisions,
            "unmapped_timestamp_fields": [
                {"name": f.name, "parsed": f.parsed, "present": f.present,
                 "needs_timezone": f.needs_timezone}
                for f in self.unmapped_fields
            ],
            "naive_timestamp_fields": [f.name for f in self.naive_fields],
            "findings": [
                {"severity": f.severity, "message": f.message, "fix": f.fix}
                for f in self.findings
            ],
            "mapped": self.mapped,
        }

    def render(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in self.counts.items() if v)
        lines = [
            f"{self.source}",
            f"  as of    {self.as_of.isoformat()}",
            f"  rows     {self.rows}",
            f"  kept     {self.kept}",
            f"  dropped  {self.dropped} ({self.drop_rate:.0%})"
            + (f"  ({counts})" if counts else ""),
        ]
        mapped = ", ".join(f"{k}={v}" for k, v in self.mapped.items() if v)
        if mapped:
            lines.append(f"  fields   {mapped}")
        if self.findings:
            lines.append("")
            lines += [f.render() for f in self.findings]
        return "\n".join(lines)


def load_rows(path: str | Path, *, results_key: str | None = None) -> list[dict[str, Any]]:
    """Read a corpus from JSON or JSONL.

    Accepts a top-level array, a wrapper object (`results_key`, or a single list
    value if there's exactly one), or one JSON object per line. Raises ValueError
    with something actionable rather than letting a JSONDecodeError escape.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc

    if not text.strip():
        raise ValueError(f"{path} is empty")

    if path.suffix == ".jsonl" or _looks_like_jsonl(text):
        return _load_jsonl(text, path)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    return _rows_from(payload, path, results_key)


def _looks_like_jsonl(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) > 1 and all(line.lstrip().startswith("{") for line in lines)


def _load_jsonl(text: str, path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} line {n} is not valid JSON: {exc}") from exc
        if not isinstance(row, Mapping):
            raise ValueError(f"{path} line {n} is a {type(row).__name__}, expected an object")
        rows.append(dict(row))
    return rows


def _rows_from(payload: Any, path: Path, results_key: str | None) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        if results_key is not None:
            found = payload.get(results_key)
            if found is None:
                keys = ", ".join(sorted(payload)) or "none"
                raise ValueError(f"{path} has no key {results_key!r}. Keys present: {keys}")
            payload = found
        else:
            lists = [v for v in payload.values() if isinstance(v, list)]
            if len(lists) != 1:
                keys = ", ".join(sorted(payload)) or "none"
                raise ValueError(
                    f"{path} is an object, not a list of records. Name the list with "
                    f"--results-key. Keys present: {keys}"
                )
            payload = lists[0]

    if not isinstance(payload, list):
        raise ValueError(f"{path} holds a {type(payload).__name__}, expected a list of objects")

    rows = []
    for i, row in enumerate(payload):
        if not isinstance(row, Mapping):
            raise ValueError(f"{path} item {i} is a {type(row).__name__}, expected an object")
        rows.append(dict(row))
    return rows


def sniff_timestamp_fields(rows: Sequence[Mapping[str, Any]]) -> list[TimestampField]:
    """Find fields that mostly hold parseable timestamps.

    Reports fields that only parse once a timezone is assumed separately,
    because those are rejected as-is and the fix is `assume_tz`, not a different
    field.
    """
    present: dict[str, int] = {}
    aware: dict[str, int] = {}
    naive: dict[str, int] = {}

    for row in rows:
        for key, value in row.items():
            if value in (None, ""):
                continue
            present[key] = present.get(key, 0) + 1
            if parse_timestamp(value) is not None:
                aware[key] = aware.get(key, 0) + 1
            elif parse_timestamp(value, assume_tz=timezone.utc) is not None:
                naive[key] = naive.get(key, 0) + 1

    fields = []
    for key, seen in present.items():
        hits = aware.get(key, 0) + naive.get(key, 0)
        if seen and hits / seen >= SNIFF_THRESHOLD:
            fields.append(
                TimestampField(
                    name=key,
                    parsed=hits,
                    present=seen,
                    needs_timezone=naive.get(key, 0) > aware.get(key, 0),
                )
            )
    return sorted(fields, key=lambda f: f.name)


def inspect_corpus(
    rows: Sequence[Mapping[str, Any]],
    as_of: str | Any,
    *,
    adapter: MappingAdapter | None = None,
    guard: TemporalGuard | None = None,
    source: str = "<rows>",
) -> CorpusReport:
    """Run the guard over a corpus and report what it would do, plus what looks wrong."""
    guard = guard if guard is not None else TemporalGuard(as_of)
    adapter = adapter if adapter is not None else MappingAdapter()

    records = adapter.to_records(list(rows))
    result = guard.filter(records)
    sniffed = sniff_timestamp_fields(rows)

    mapped = {
        "content": "+".join(adapter.content_keys),
        "source": "|".join(adapter.source_keys),
        "published": "|".join(adapter.published_keys),
        "updated": "|".join(adapter.updated_keys) or None,
    }
    report = CorpusReport(
        source=source,
        as_of=guard.as_of,
        rows=len(records),
        counts=result.counts,
        unmapped_fields=[f for f in sniffed if f.name not in _consumed(adapter)],
        naive_fields=[f for f in sniffed if f.needs_timezone],
        unparseable_revisions=sum(1 for r in records if r.updated_at_raw is not None),
        mapped={k: v for k, v in mapped.items() if v},
    )
    report.findings = _findings(report, records, guard)
    return report


def _consumed(adapter: MappingAdapter) -> set[str]:
    """Every field name the adapter already reads."""
    return (
        set(adapter.published_keys)
        | set(adapter.updated_keys)
        | set(adapter.retrieved_keys)
        | set(adapter.source_keys)
    )


def _findings(report: CorpusReport, records: list[Any], guard: TemporalGuard) -> list[Finding]:
    """The advice. Ordered most severe first."""
    findings: list[Finding] = []
    rows = report.rows

    if rows == 0:
        return [Finding(severity="error", message="the corpus is empty")]

    if report.kept == 0:
        findings.append(
            Finding(
                severity="error",
                message=f"the guard drops all {rows} record(s), so an agent would get no evidence at all",
                fix=(
                    "Usually a wrong --published-key or an as-of before the corpus starts, "
                    "not a corpus that is genuinely all future. Check the fields line above."
                ),
            )
        )
    elif report.kept == rows:
        findings.append(
            Finding(
                severity="error",
                message=f"the guard drops nothing, all {rows} record(s) predate the as-of date",
                fix=(
                    "A clean run on this corpus proves nothing, because the guard never had "
                    "anything to withhold. Move --as-of earlier, or use a corpus that "
                    "straddles the date."
                ),
            )
        )

    undated = report.counts.get(Verdict.UNDATED.value, 0)
    unparseable = report.counts.get(Verdict.UNPARSEABLE.value, 0)
    if undated + unparseable:
        share = (undated + unparseable) / rows
        severity: Severity = "warning" if share >= 0.2 else "note"
        findings.append(
            Finding(
                severity=severity,
                message=(
                    f"{undated + unparseable} of {rows} record(s) ({share:.0%}) have no usable "
                    "publication date, so they are rejected on sight"
                ),
                fix=_undated_fix(report, share),
            )
        )

    for field in report.unmapped_fields:
        if field.looks_like_a_revision:
            findings.append(
                Finding(
                    severity="warning",
                    message=(
                        f"{field.name!r} looks like a revision timestamp and is not mapped, "
                        f"so it is sitting in metadata where the guard never looks"
                    ),
                    fix=(
                        f"Pass --updated-key {field.name}. A page published before the as-of "
                        "date and edited after it reads as old content otherwise."
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    severity="note",
                    message=(
                        f"{field.name!r} parses as a timestamp in {field.parsed} of "
                        f"{field.present} record(s) and is not mapped"
                    ),
                )
            )

    if report.unparseable_revisions:
        findings.append(
            Finding(
                severity="note",
                message=(
                    f"{report.unparseable_revisions} record(s) have a revision timestamp that "
                    "does not parse, so the guard cannot tell whether they were edited"
                ),
                fix="A well-dated record is still admitted. Check the format if that surprises you.",
            )
        )

    if guard.allow_undated and undated + unparseable:
        findings.append(
            Finding(
                severity="warning",
                message=(
                    f"allow_undated is on, so {undated + unparseable} record(s) with no usable "
                    "date reach the agent unchecked"
                ),
                fix="Anything written after the as-of date but undated gets through this way.",
            )
        )

    return findings


def _undated_fix(report: CorpusReport, share: float) -> str:
    """Advice for undated records, which depends on whether the mapping is at fault.

    A handful of undated rows in a corpus that otherwise dates fine is just how
    the data is. Most of the corpus coming back undated means the publication
    field is probably named something else.
    """
    if report.naive_fields:
        # Covers a mapped publication field too, which is the common version of
        # this: every record dated, every date rejected for having no offset.
        names = ", ".join(f.name for f in report.naive_fields)
        return (
            f"{names} hold timezone-naive timestamps. A wall clock with no offset is not an "
            "instant, so pass assume_tz on the adapter if you know the corpus timezone."
        )
    if share >= 0.5:
        # The mapping is the likely culprit. Revision fields are never the fix
        # for an undated record, so don't offer them.
        candidates = [f.name for f in report.unmapped_fields if not f.looks_like_a_revision]
        if candidates:
            return (
                f"Most of the corpus is undated, so --published-key is probably wrong. "
                f"These parse as timestamps: {', '.join(candidates)}."
            )
    return "Records with no date cannot be shown to predate the cutoff, so they are dropped."
