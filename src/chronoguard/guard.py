"""The temporal filter.

`TemporalGuard` takes a list of `EvidenceRecord`s and an as-of instant, and
decides what the agent is allowed to see. This is the whole of Layer 1 (see
docs/kb/two-leakage-channels.md): it contains tool leakage and nothing else. It has no idea what the
model knows.

## The boundary rule

A record is allowed when `published_at < as_of`, strictly. A record published at
exactly `as_of` is rejected.

That's deliberate. Loads of real corpora store dates at day precision, so
"published 2023-06-01" becomes `2023-06-01T00:00:00Z`. If the boundary were
inclusive and you set `as_of` to midnight on the 1st, every document published
anywhere in that day would sail through, including ones written hours after the
moment you're trying to simulate. Exclusive means those get dropped instead.
Losing a record published on the exact microsecond of the cutoff costs you
nothing. Admitting a day's worth of hindsight costs you the whole experiment.

If you actually want everything up to the end of a day, say so in `as_of`: pass
`2023-06-02T00:00:00Z` rather than the 1st.

## Revisions

`published_at` alone is not enough for mutable sources. A wiki page created in
2022 and rewritten in 2024 has a 2022 publication date and 2024 content, and a
guard that only reads the creation date hands the agent two years of hindsight.
Confluence, Notion, SharePoint, Git and every CMS worth the name carry a
modified date; the fix is to use it.

So when a record has an `updated_at` later than as_of, the default is to reject
it under `Verdict.REVISED`, on the same exclusive boundary. Pass
`revisions="ignore"` to filter on publication date only.

This costs nothing on an immutable corpus, because a record with no `updated_at`
is unaffected. You only see the new verdict once your adapter starts mapping a
revision field.

One asymmetry worth knowing about. An unparseable `published_at` gets the record
rejected, because nothing then shows the content predates the cutoff. An
unparseable `updated_at` on an otherwise clean record does not, because
`published_at` already proved the content existed in time and all we've lost is
whether it was later edited. Rejecting there would gut any real corpus over a
few junk fields. `chronoguard check` counts them so they don't go unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

from chronoguard.evidence import EvidenceRecord, parse_timestamp

__all__ = [
    "FilterResult",
    "GuardPolicy",
    "Judgement",
    "RevisionPolicy",
    "TemporalGuard",
    "Verdict",
]


class GuardPolicy(str, Enum):
    """What to do with a record that violates the as-of boundary."""

    STRICT = "strict"
    """Drop it. The agent never sees it."""

    WARN = "warn"
    """Keep it but flag it. Useful for measuring how much leakage a corpus
    would have caused without actually blinding the run."""


class Verdict(str, Enum):
    """Why a record was allowed or rejected."""

    ALLOWED = "allowed"
    """Published strictly before as_of."""

    FUTURE = "future"
    """Published at or after as_of. This is the leak the guard exists to stop."""

    UNDATED = "undated"
    """No publication timestamp at all."""

    UNPARSEABLE = "unparseable"
    """A timestamp was supplied but it isn't a usable instant."""

    REVISED = "revised"
    """Published before as_of, but edited at or after it. The text the agent
    would have read is not the text that existed at the as-of instant."""


UNDATED_VERDICTS = frozenset({Verdict.UNDATED, Verdict.UNPARSEABLE})


class RevisionPolicy(str, Enum):
    """What to do about a record's `updated_at`."""

    REJECT = "reject"
    """Treat an edit at or after as_of as a boundary violation."""

    IGNORE = "ignore"
    """Filter on publication date only. Pre-0.2 behaviour."""


class Judgement(BaseModel):
    """The guard's decision about one record, with the reasoning attached."""

    model_config = ConfigDict(extra="forbid")

    record: EvidenceRecord
    verdict: Verdict
    kept: bool
    reason: str

    @property
    def is_violation(self) -> bool:
        """True when the record broke the boundary, whether or not it was kept.

        Under `warn` a violation is kept, so don't read `kept` as "clean".
        """
        return self.verdict is not Verdict.ALLOWED


class FilterResult(BaseModel):
    """Everything one filtering pass decided, agent-facing output plus the audit trail."""

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    policy: GuardPolicy
    judgements: list[Judgement]

    @property
    def kept(self) -> list[EvidenceRecord]:
        """The records the agent is allowed to see."""
        return [j.record for j in self.judgements if j.kept]

    @property
    def dropped(self) -> list[EvidenceRecord]:
        """The records that were withheld."""
        return [j.record for j in self.judgements if not j.kept]

    @property
    def violations(self) -> list[Judgement]:
        """Every judgement that broke the boundary, kept or not."""
        return [j for j in self.judgements if j.is_violation]

    @property
    def total(self) -> int:
        return len(self.judgements)

    @property
    def kept_count(self) -> int:
        return sum(1 for j in self.judgements if j.kept)

    @property
    def filtered_count(self) -> int:
        """How many records were withheld from the agent."""
        return self.total - self.kept_count

    @property
    def violation_count(self) -> int:
        """How many records broke the boundary, including ones `warn` let through."""
        return len(self.violations)

    @property
    def counts(self) -> dict[str, int]:
        """Records per verdict, every verdict present even when zero."""
        tally = {v.value: 0 for v in Verdict}
        for j in self.judgements:
            tally[j.verdict.value] += 1
        return tally

    def summary(self) -> str:
        """One line fit for a log or a report header."""
        parts = ", ".join(f"{name}={n}" for name, n in self.counts.items() if n)
        return (
            f"as_of={self.as_of.isoformat()} policy={self.policy.value} "
            f"kept {self.kept_count}/{self.total}, filtered {self.filtered_count}"
            + (f" ({parts})" if parts else "")
        )


class TemporalGuard:
    """Filters evidence down to what existed before `as_of`.

    Args:
        as_of: The instant being simulated. Must be timezone-aware. A string is
            parsed with the same rules as evidence timestamps, so it needs an
            explicit offset.
        policy: `strict` drops violations, `warn` keeps and flags them.
        allow_undated: Off by default. Records with no usable publication
            timestamp are rejected, because you can't prove they predate the
            cutoff. Turn it on only when you know your corpus.
        revisions: `reject` (the default) treats an `updated_at` at or after
            as_of as a violation, because the agent would be reading text that
            didn't exist yet. `ignore` filters on publication date only.
            Records with no `updated_at` are unaffected either way.
    """

    def __init__(
        self,
        as_of: datetime | str,
        *,
        policy: GuardPolicy | str = GuardPolicy.STRICT,
        allow_undated: bool = False,
        revisions: RevisionPolicy | str = RevisionPolicy.REJECT,
    ) -> None:
        parsed = parse_timestamp(as_of)
        if parsed is None:
            raise ValueError(
                f"as_of must be a timezone-aware instant, got {as_of!r}. "
                "Add an explicit offset, for example '2023-06-01T00:00:00Z'."
            )
        self.as_of = parsed
        self.policy = GuardPolicy(policy)
        self.allow_undated = allow_undated
        self.revisions = RevisionPolicy(revisions)

    def __repr__(self) -> str:
        return (
            f"TemporalGuard(as_of={self.as_of.isoformat()!r}, "
            f"policy={self.policy.value!r}, allow_undated={self.allow_undated}, "
            f"revisions={self.revisions.value!r})"
        )

    def judge(self, record: EvidenceRecord) -> Judgement:
        """Decide one record's fate."""
        verdict, reason = self._assess(record)
        return Judgement(
            record=record,
            verdict=verdict,
            kept=self._keep(verdict),
            reason=reason,
        )

    def filter(self, records: Iterable[EvidenceRecord]) -> FilterResult:
        """Judge every record and bundle up the decisions."""
        return FilterResult(
            as_of=self.as_of,
            policy=self.policy,
            judgements=[self.judge(r) for r in records],
        )

    def allows(self, record: EvidenceRecord) -> bool:
        """Shorthand for `judge(record).kept`."""
        return self.judge(record).kept

    def _assess(self, record: EvidenceRecord) -> tuple[Verdict, str]:
        verdict, reason = self._assess_published(record)
        if not self._admissible(verdict):
            # The publication date already sank it. Naming the revision on top
            # of that buries the more fundamental problem, so leave the first
            # reason standing.
            return verdict, reason
        return self._assess_revision(record, verdict, reason)

    def _admissible(self, verdict: Verdict) -> bool:
        """Whether this verdict clears the publication check on its own merits.

        Deliberately not `_keep`: under `warn` everything is kept, and warn is
        meant to change what the agent sees, not what the guard diagnoses. A
        revised record gets the same verdict under both policies.
        """
        if verdict is Verdict.ALLOWED:
            return True
        return self.allow_undated and verdict in UNDATED_VERDICTS

    def _assess_published(self, record: EvidenceRecord) -> tuple[Verdict, str]:
        published = record.published_at
        if published is None:
            verdict = Verdict.UNPARSEABLE if record.published_at_raw else Verdict.UNDATED
            reason = record.undated_reason or "no publication timestamp"
            if self.allow_undated:
                return verdict, f"{reason}; admitted because allow_undated is on"
            return verdict, f"{reason}, so it can't be shown to predate as_of"

        if published < self.as_of:
            return Verdict.ALLOWED, f"published {published.isoformat()}, before as_of"

        relation = "at" if published == self.as_of else "after"
        return (
            Verdict.FUTURE,
            f"published {published.isoformat()}, {relation} as_of "
            f"{self.as_of.isoformat()} (boundary is exclusive)",
        )

    def _assess_revision(
        self, record: EvidenceRecord, verdict: Verdict, reason: str
    ) -> tuple[Verdict, str]:
        """Escalate a would-be-kept record that was edited after as_of."""
        if self.revisions is RevisionPolicy.IGNORE:
            return verdict, reason

        updated = record.updated_at
        if updated is None or updated < self.as_of:
            return verdict, reason

        relation = "at" if updated == self.as_of else "after"
        return (
            Verdict.REVISED,
            f"{reason}, but last edited {updated.isoformat()}, {relation} as_of "
            f"{self.as_of.isoformat()}, so its text carries hindsight",
        )

    def _keep(self, verdict: Verdict) -> bool:
        if verdict is Verdict.ALLOWED:
            return True
        if self.policy is GuardPolicy.WARN:
            return True
        return self.allow_undated and verdict in UNDATED_VERDICTS


def guard_records(
    records: Iterable[EvidenceRecord],
    as_of: datetime | str,
    **kwargs: Any,
) -> FilterResult:
    """One-shot convenience wrapper around `TemporalGuard.filter`."""
    return TemporalGuard(as_of, **kwargs).filter(records)
