"""ChronoGuard: a point-in-time leakage guard for LLM agents.

ChronoGuard runs an LLM agent as if it were operating at a past date, then
measures how well that blinding actually holds.

Two separate leakage channels, handled separately (see DESIGN.md):

* **Tool leakage**: the agent retrieves evidence published after the as-of date.
  Fixable by filtering, which is `chronoguard.evidence` and `chronoguard.guard`.
* **Parametric leakage**: the model's weights already encode post-as-of facts.
  Filtering can't touch it, so it gets probed and measured instead.

Quick start::

    from chronoguard import EvidenceRecord, TemporalGuard

    guard = TemporalGuard("2023-06-01T00:00:00Z")
    result = guard.filter([
        EvidenceRecord.from_source("...", "doc-1", published_at="2023-05-04T09:00:00Z"),
        EvidenceRecord.from_source("...", "doc-2", published_at="2023-08-11T09:00:00Z"),
    ])
    result.kept             # only doc-1
    result.filtered_count   # 1

Wrap a tool so the agent can only ever see the survivors::

    from chronoguard import MappingAdapter, guarded_tool

    @guarded_tool(guard, MappingAdapter(source_key="url", published_key="date"))
    def web_search(query: str) -> list[dict]:
        ...
"""

from chronoguard.evidence import EvidenceRecord, parse_timestamp
from chronoguard.guard import (
    FilterResult,
    GuardPolicy,
    Judgement,
    TemporalGuard,
    Verdict,
    guard_records,
)
from chronoguard.interception import (
    AuditLog,
    EvidenceAdapter,
    GuardedTool,
    MappingAdapter,
    RecordAdapter,
    ToolCall,
    guard_tool,
    guarded_tool,
)

__version__ = "0.1.0"

__all__ = [
    "AuditLog",
    "EvidenceAdapter",
    "EvidenceRecord",
    "FilterResult",
    "GuardPolicy",
    "GuardedTool",
    "Judgement",
    "MappingAdapter",
    "RecordAdapter",
    "TemporalGuard",
    "ToolCall",
    "Verdict",
    "__version__",
    "guard_records",
    "guard_tool",
    "guarded_tool",
    "parse_timestamp",
]
