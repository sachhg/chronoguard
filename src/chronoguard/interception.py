"""Tool-call interception.

Wrap any Python callable that returns evidence and the agent only ever sees
what the guard allows. The wrapper handles three jobs:

1. Run the real tool.
2. Push its output through an adapter into `EvidenceRecord`s, then through the
   `TemporalGuard`.
3. Hand the agent the survivors, and file the full decision in an audit log so
   reporting can say how much got dropped.

The adapter is the part that makes this work for arbitrary tools. Search APIs,
vector stores and SQL rows all have different field names, so each tool brings a
small mapping and the filtering logic stays in one place.

    guard = TemporalGuard("2023-06-01T00:00:00Z")
    audit = AuditLog()

    @guarded_tool(guard, MappingAdapter(source_key="url", published_key="date"), audit=audit)
    def web_search(query: str) -> list[dict]:
        ...

    web_search("meridian pricing")   # only pre-as-of hits come back
    audit.filtered_count             # how many got dropped, for the report

Only wrap tools that return evidence. A calculator has nothing to filter and
wrapping it just hands the agent an empty list.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import FilterResult, TemporalGuard, Verdict

__all__ = [
    "AuditLog",
    "CallableAdapter",
    "EvidenceAdapter",
    "GuardedTool",
    "MappingAdapter",
    "RecordAdapter",
    "ToolCall",
    "guard_tool",
    "guarded_tool",
    "resolve_adapter",
]


@runtime_checkable
class EvidenceAdapter(Protocol):
    """Turns one tool's native output into evidence records."""

    def to_records(self, raw: Any) -> list[EvidenceRecord]: ...


class RecordAdapter:
    """For tools that already return `EvidenceRecord`s, one or many."""

    def to_records(self, raw: Any) -> list[EvidenceRecord]:
        if raw is None:
            return []
        if isinstance(raw, EvidenceRecord):
            return [raw]
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes, Mapping)):
            records = list(raw)
            bad = [r for r in records if not isinstance(r, EvidenceRecord)]
            if bad:
                raise TypeError(
                    f"RecordAdapter expected EvidenceRecords, got {type(bad[0]).__name__}. "
                    "Use MappingAdapter for dict-shaped output, or pass your own adapter."
                )
            return records
        raise TypeError(f"RecordAdapter cannot handle {type(raw).__name__}")


class CallableAdapter:
    """Wraps a plain function of raw output into the adapter interface."""

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self._fn = fn

    def to_records(self, raw: Any) -> list[EvidenceRecord]:
        return RecordAdapter().to_records(self._fn(raw))


class MappingAdapter:
    """Maps dict-shaped tool output onto evidence records.

    Args:
        content_key: Field holding the text, or several fields to stitch
            together in order (title then snippet, say). Missing ones are
            skipped.
        source_key: Field holding a stable id. Several may be given, first hit
            wins.
        published_key: Field holding the publication timestamp. Several may be
            given, first hit wins.
        retrieved_key: Optional field for when your pipeline fetched it.
        results_key: If the tool returns a wrapper dict like
            `{"results": [...]}`, the key to dig into.
        metadata_keys: Which leftover fields to keep as metadata. Default is
            everything not already consumed.
        assume_tz: Timezone to assume for naive timestamps. Left off, naive
            timestamps are treated as unusable and the record gets rejected.
        separator: Joins multiple content fields.
    """

    def __init__(
        self,
        *,
        content_key: str | Sequence[str] = "content",
        source_key: str | Sequence[str] = ("source_id", "id", "url"),
        published_key: str | Sequence[str] = ("published_at", "published", "date"),
        retrieved_key: str | Sequence[str] | None = None,
        results_key: str | None = None,
        metadata_keys: Sequence[str] | None = None,
        assume_tz: Any = None,
        separator: str = "\n",
    ) -> None:
        self.content_keys = _as_tuple(content_key)
        self.source_keys = _as_tuple(source_key)
        self.published_keys = _as_tuple(published_key)
        self.retrieved_keys = _as_tuple(retrieved_key) if retrieved_key else ()
        self.results_key = results_key
        self.metadata_keys = tuple(metadata_keys) if metadata_keys is not None else None
        self.assume_tz = assume_tz
        self.separator = separator

    def to_records(self, raw: Any) -> list[EvidenceRecord]:
        return [self._one(item, i) for i, item in enumerate(self._items(raw))]

    def _items(self, raw: Any) -> list[Mapping[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, Mapping):
            if self.results_key is not None:
                return list(raw.get(self.results_key) or [])
            return [raw]
        if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
            return list(raw)
        raise TypeError(f"MappingAdapter cannot handle {type(raw).__name__}")

    def _one(self, item: Any, index: int) -> EvidenceRecord:
        if isinstance(item, EvidenceRecord):
            return item
        if not isinstance(item, Mapping):
            raise TypeError(
                f"MappingAdapter expected dict-shaped results, got {type(item).__name__}"
            )

        parts = [str(item[k]) for k in self.content_keys if item.get(k) not in (None, "")]
        consumed = set(self.content_keys) | set(self.source_keys) | set(self.published_keys)
        consumed |= set(self.retrieved_keys)

        if self.metadata_keys is None:
            metadata = {k: v for k, v in item.items() if k not in consumed}
        else:
            metadata = {k: item[k] for k in self.metadata_keys if k in item}

        return EvidenceRecord.from_source(
            self.separator.join(parts),
            str(_first(item, self.source_keys, default=f"record-{index}")),
            published_at=_first(item, self.published_keys),
            retrieved_at=_first(item, self.retrieved_keys),
            metadata=metadata,
            assume_tz=self.assume_tz,
        )


def _as_tuple(value: str | Sequence[str]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def _first(item: Mapping[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item[key]
    return default


def resolve_adapter(adapter: EvidenceAdapter | Callable[[Any], Any] | None) -> EvidenceAdapter:
    """Accept an adapter, a plain function, or nothing at all."""
    if adapter is None:
        return RecordAdapter()
    if isinstance(adapter, EvidenceAdapter):
        return adapter
    if callable(adapter):
        return CallableAdapter(adapter)
    raise TypeError(f"{adapter!r} is not an adapter, a callable, or None")


class ToolCall(BaseModel):
    """One guarded invocation, kept for reporting."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: FilterResult

    @property
    def kept_count(self) -> int:
        return self.result.kept_count

    @property
    def filtered_count(self) -> int:
        return self.result.filtered_count


class AuditLog(BaseModel):
    """Every guarded tool call in a run, so the report can total it up.

    Share one of these across all the tools an agent gets, then read the
    numbers off it once the run finishes.
    """

    model_config = ConfigDict(extra="forbid")

    calls: list[ToolCall] = Field(default_factory=list)

    def record(self, call: ToolCall) -> ToolCall:
        self.calls.append(call)
        return call

    def clear(self) -> None:
        self.calls.clear()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def total(self) -> int:
        """Records seen across every call, before filtering."""
        return sum(c.result.total for c in self.calls)

    @property
    def kept_count(self) -> int:
        return sum(c.kept_count for c in self.calls)

    @property
    def filtered_count(self) -> int:
        """Records withheld from the agent across every call."""
        return sum(c.filtered_count for c in self.calls)

    @property
    def violation_count(self) -> int:
        """Boundary breaks across every call, including any warn let through."""
        return sum(c.result.violation_count for c in self.calls)

    @property
    def counts(self) -> dict[str, int]:
        tally = {v.value: 0 for v in Verdict}
        for call in self.calls:
            for name, n in call.result.counts.items():
                tally[name] += n
        return tally

    def by_tool(self) -> dict[str, dict[str, int]]:
        """Per-tool totals, for reports that want to name the leaky tool."""
        out: dict[str, dict[str, int]] = {}
        for call in self.calls:
            row = out.setdefault(call.tool, {"calls": 0, "total": 0, "kept": 0, "filtered": 0})
            row["calls"] += 1
            row["total"] += call.result.total
            row["kept"] += call.kept_count
            row["filtered"] += call.filtered_count
        return out

    def summary(self) -> str:
        parts = ", ".join(f"{name}={n}" for name, n in self.counts.items() if n)
        return (
            f"{self.call_count} guarded call(s), kept {self.kept_count}/{self.total}, "
            f"filtered {self.filtered_count}" + (f" ({parts})" if parts else "")
        )


class GuardedTool:
    """A tool callable with the temporal filter bolted onto its return value.

    Call it exactly like the function it wraps. The name, docstring and
    signature are copied across so agent frameworks can still build a schema
    from it.

    Args:
        fn: The real tool.
        guard: The filter to run its output through.
        adapter: Maps the tool's output into evidence records. A plain callable
            works too. Left off, the tool is expected to return records already.
        name: Overrides the tool name used in the audit log.
        audit: Shared log to file calls in. One is created if you don't pass one.
        render: Turns the `FilterResult` into whatever the agent should see.
            Default hands back the list of surviving records.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        guard: TemporalGuard,
        adapter: EvidenceAdapter | Callable[[Any], Any] | None = None,
        *,
        name: str | None = None,
        audit: AuditLog | None = None,
        render: Callable[[FilterResult], Any] | None = None,
    ) -> None:
        self.fn = fn
        self.guard = guard
        self.adapter = resolve_adapter(adapter)
        self.audit = audit if audit is not None else AuditLog()
        self.render = render
        self.name = name or getattr(fn, "__name__", fn.__class__.__name__)
        functools.update_wrapper(self, fn)
        self.__name__ = self.name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raw = self.fn(*args, **kwargs)
        result = self.guard.filter(self.adapter.to_records(raw))
        self.audit.record(
            ToolCall(
                tool=self.name,
                arguments=_describe_arguments(self.fn, args, kwargs),
                result=result,
            )
        )
        return self.render(result) if self.render else result.kept

    def __repr__(self) -> str:
        return f"GuardedTool({self.name!r}, as_of={self.guard.as_of.isoformat()!r})"

    @property
    def calls(self) -> list[ToolCall]:
        """This tool's calls, pulled out of a possibly shared audit log."""
        return [c for c in self.audit.calls if c.tool == self.name]

    @property
    def last_result(self) -> FilterResult | None:
        calls = self.calls
        return calls[-1].result if calls else None

    @property
    def filtered_count(self) -> int:
        """Records this tool withheld from the agent."""
        return sum(c.filtered_count for c in self.calls)

    @property
    def kept_count(self) -> int:
        return sum(c.kept_count for c in self.calls)


def guard_tool(
    fn: Callable[..., Any],
    guard: TemporalGuard,
    adapter: EvidenceAdapter | Callable[[Any], Any] | None = None,
    **kwargs: Any,
) -> GuardedTool:
    """Wrap an existing tool. The higher-order form."""
    return GuardedTool(fn, guard, adapter, **kwargs)


def guarded_tool(
    guard: TemporalGuard,
    adapter: EvidenceAdapter | Callable[[Any], Any] | None = None,
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], GuardedTool]:
    """Wrap a tool at definition time. The decorator form."""

    def decorate(fn: Callable[..., Any]) -> GuardedTool:
        return GuardedTool(fn, guard, adapter, **kwargs)

    return decorate


def _describe_arguments(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Best-effort record of how the tool was called, always JSON-safe."""
    try:
        bound = inspect.signature(fn).bind(*args, **kwargs)
        bound.apply_defaults()
        raw = dict(bound.arguments)
    except (TypeError, ValueError):
        raw = {"args": list(args), "kwargs": dict(kwargs)}
    return {k: _jsonable(v) for k, v in raw.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return repr(value)
