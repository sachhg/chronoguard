"""The canonical evidence record.

Everything the agent is allowed to see passes through this type first. Tools
return wildly different shapes (search hits, DB rows, API payloads), so each
tool gets an adapter that maps its output into `EvidenceRecord`s and the
filtering logic only ever has to understand one thing.

A record carries two dates the guard can act on. `published_at` is when the
content first existed. `updated_at` is when it was last revised, which matters
because a wiki page written in 2022 and rewritten in 2024 is not 2022 content
any more. See `chronoguard.guard` for what the guard does with each.

Two ways to build a record:

* `EvidenceRecord(...)` is strict. A naive or unparseable `published_at` raises.
  Use it when you control the data.
* `EvidenceRecord.from_source(...)` is lenient and never raises on a bad
  timestamp. It keeps whatever it was given in `published_at_raw` and leaves
  `published_at` as None, so the guard can reject the record and say why. Use it
  when you're adapting real tool output.
"""

from __future__ import annotations

from datetime import date, datetime, timezone, tzinfo
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = ["EvidenceRecord", "parse_timestamp"]


def parse_timestamp(value: Any, *, assume_tz: tzinfo | None = None) -> datetime | None:
    """Best effort conversion of `value` into a timezone-aware datetime.

    Returns None when the value can't be turned into an unambiguous instant.
    That includes junk strings, empty values, and (unless `assume_tz` is given)
    anything timezone-naive, because a wall-clock time without an offset isn't a
    point on the timeline and guessing one is how you get off-by-a-day leaks.

    Accepts datetimes, dates, ISO 8601 strings (including a trailing Z), and
    int/float epoch seconds.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        # bool is an int subclass and 1970-01-01 is never what anyone meant.
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed
    if assume_tz is not None:
        return parsed.replace(tzinfo=assume_tz)
    return None


class EvidenceRecord(BaseModel):
    """One piece of evidence on its way to the agent.

    `published_at` is when the world could first have seen this content.
    `updated_at` is when it was last revised, and is filtered on too: content
    edited after the as-of instant carries hindsight no matter how old the page
    is. `retrieved_at` is when your pipeline fetched it, which is almost always
    "now" and is never filtered on, it's there for audit trails.
    """

    model_config = ConfigDict(extra="forbid")

    content: str
    source_id: str
    published_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    published_at_raw: str | None = Field(
        default=None,
        description="Whatever the source gave us when we couldn't parse it, kept for reporting.",
    )
    updated_at_raw: str | None = Field(
        default=None,
        description="Same, for a revision timestamp we couldn't parse.",
    )

    @classmethod
    def from_source(
        cls,
        content: str,
        source_id: str,
        *,
        published_at: Any = None,
        updated_at: Any = None,
        retrieved_at: Any = None,
        metadata: dict[str, Any] | None = None,
        assume_tz: tzinfo | None = None,
    ) -> EvidenceRecord:
        """Build a record from raw tool output without blowing up on bad dates."""
        published, published_raw = _keep_raw(published_at, assume_tz)
        updated, updated_raw = _keep_raw(updated_at, assume_tz)
        return cls(
            content=content,
            source_id=source_id,
            published_at=published,
            updated_at=updated,
            retrieved_at=parse_timestamp(retrieved_at, assume_tz=assume_tz),
            metadata=dict(metadata or {}),
            published_at_raw=published_raw,
            updated_at_raw=updated_raw,
        )

    @property
    def is_dated(self) -> bool:
        """True when we have a usable publication instant to filter on."""
        return self.published_at is not None

    @property
    def undated_reason(self) -> str | None:
        """Why this record can't be dated, or None if it can."""
        if self.published_at is not None:
            return None
        if self.published_at_raw is not None:
            return f"could not parse publication timestamp {self.published_at_raw!r}"
        return "no publication timestamp"

    @property
    def is_revised(self) -> bool:
        """True when we know this content was edited after it was published.

        An `updated_at` at or before `published_at` doesn't count: plenty of
        systems stamp both on creation, and that isn't a revision.
        """
        if self.updated_at is None:
            return False
        if self.published_at is None:
            return True
        return self.updated_at > self.published_at

    @property
    def latest_instant(self) -> datetime | None:
        """The most recent moment we can prove this content existed in its current form.

        The revision date when there is one, the publication date otherwise.
        This is what the guard compares against as_of when revisions are being
        enforced.
        """
        stamps = [s for s in (self.published_at, self.updated_at) if s is not None]
        return max(stamps) if stamps else None


def _keep_raw(value: Any, assume_tz: tzinfo | None) -> tuple[datetime | None, str | None]:
    """Parse a timestamp, keeping the original text when it doesn't parse."""
    parsed = parse_timestamp(value, assume_tz=assume_tz)
    if parsed is None and value is not None and value != "":
        return None, str(value)
    return parsed, None
