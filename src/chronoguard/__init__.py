"""ChronoGuard: a point-in-time leakage guard for LLM agents.

ChronoGuard runs an LLM agent as if it were operating at a past date, then
measures how well that blinding actually holds.

Two separate leakage channels, handled separately (see docs/kb/two-leakage-channels.md):

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

`async def` tools work the same way, you just await the wrapped version. On a
mutable source, name the revision field too, or a page written in 2022 and
rewritten in 2024 counts as 2022 content::

    MappingAdapter(source_key="url", published_key="date", updated_key="modified")
"""

from chronoguard._version import __version__
from chronoguard.agent import AgentConfig, AgentRun, AgentRunner, run_agent
from chronoguard.claims import (
    Claim,
    ClaimClassifier,
    ClaimLabel,
    ClaimReport,
    classify_run,
)
from chronoguard.evidence import EvidenceRecord, parse_timestamp
from chronoguard.guard import (
    FilterResult,
    GuardPolicy,
    Judgement,
    RevisionPolicy,
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
from chronoguard.backends import (
    BackendTimeout,
    BackendUnavailable,
    ChatBackend,
    OpenAICompatClient,
)
from chronoguard.ollama import OllamaClient, OllamaUnavailable
from chronoguard.preflight import CorpusReport, inspect_corpus, load_rows
from chronoguard.probe import (
    CaseIssue,
    CaseSetReport,
    CutoffRisk,
    LeakageProbe,
    ModelCutoffs,
    ProbeCase,
    ProbeReport,
    describe_cases,
    load_model_cutoffs,
    load_probe_cases,
)
from chronoguard.report import ScenarioConfig, ScenarioReport, run_scenario

__all__ = [
    "AgentConfig",
    "AgentRun",
    "AgentRunner",
    "AuditLog",
    "BackendTimeout",
    "BackendUnavailable",
    "CaseIssue",
    "CaseSetReport",
    "ChatBackend",
    "Claim",
    "ClaimClassifier",
    "ClaimLabel",
    "ClaimReport",
    "CorpusReport",
    "CutoffRisk",
    "EvidenceAdapter",
    "EvidenceRecord",
    "FilterResult",
    "GuardPolicy",
    "GuardedTool",
    "Judgement",
    "LeakageProbe",
    "MappingAdapter",
    "ModelCutoffs",
    "OllamaClient",
    "OllamaUnavailable",
    "OpenAICompatClient",
    "ProbeCase",
    "ProbeReport",
    "RecordAdapter",
    "RevisionPolicy",
    "ScenarioConfig",
    "ScenarioReport",
    "TemporalGuard",
    "ToolCall",
    "Verdict",
    "__version__",
    "classify_run",
    "describe_cases",
    "guard_records",
    "guard_tool",
    "guarded_tool",
    "inspect_corpus",
    "load_model_cutoffs",
    "load_probe_cases",
    "load_rows",
    "parse_timestamp",
    "run_agent",
    "run_scenario",
]
