"""A minimal agent loop that runs against local Ollama models.

Two modes, picked automatically from what the model can do:

* **native**: the model gets real tool definitions and replies with
  `tool_calls`. Cleaner, but only some models support it.
* **react**: the model is asked to reply with one JSON object per turn, either
  `{"tool": ..., "arguments": {...}}` or `{"answer": ...}`. Text parsing, works
  with anything that can follow instructions.

The tools handed in are already guarded, so the loop never has to think about
dates. Worth being explicit about one thing: the "you are operating as of X"
line in the system prompt is not the containment mechanism. It's there to keep
the model on task. The containment is the guard sitting in front of the tools.
Prompts are a request, the filter is a wall.

The agent is never told what got filtered. Telling it "4 documents were withheld
because they postdate your cutoff" is itself a hint that the future exists and
has interesting things in it. Those counts go to the report, not the model.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from chronoguard.evidence import EvidenceRecord
from chronoguard.interception import AuditLog, GuardedTool
from chronoguard.ollama import ChatMessage, OllamaClient

__all__ = [
    "AgentConfig",
    "AgentRun",
    "AgentRunner",
    "AgentStep",
    "extract_json_object",
    "format_evidence",
    "run_agent",
    "tool_schema",
]

MAX_CHARS_PER_RECORD = 600


class AgentConfig(BaseModel):
    """Everything a run needs. The whole config surface for phase 3."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    task: str
    as_of: AwareDatetime
    model: str | None = Field(
        default=None,
        description="Installed Ollama model. Left unset, one is discovered at runtime.",
    )
    mode: Literal["auto", "native", "react"] = "auto"
    max_steps: int = Field(default=6, ge=1)
    temperature: float = 0.0
    max_format_retries: int = Field(
        default=2,
        ge=0,
        description="How many times to nudge a model that replies with unparseable text.",
    )


class AgentStep(BaseModel):
    """One thing that happened in the loop."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_call", "answer", "nudge", "error"]
    text: str = ""
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    kept_count: int = 0
    filtered_count: int = 0


class AgentRun(BaseModel):
    """The result of a run, including everything the agent actually saw."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    config: AgentConfig
    model: str
    mode: Literal["native", "react"]
    steps: list[AgentStep] = Field(default_factory=list)
    final_answer: str = ""
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    audit: AuditLog = Field(default_factory=AuditLog)
    stopped_because: Literal["answered", "max_steps", "error"] = "answered"

    @property
    def tool_calls(self) -> list[AgentStep]:
        return [s for s in self.steps if s.kind == "tool_call"]

    @property
    def used_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def evidence_text(self) -> str:
        """Everything the agent was shown, for grepping and for claim checks."""
        return "\n".join(f"{r.source_id}\n{r.content}" for r in self.evidence)

    def summary(self) -> str:
        return (
            f"{self.model} [{self.mode}] {len(self.tool_calls)} tool call(s), "
            f"{len(self.evidence)} record(s) seen, {self.audit.filtered_count} filtered, "
            f"stopped: {self.stopped_because}"
        )


def tool_schema(tool: Any, name: str | None = None) -> dict[str, Any]:
    """Build an Ollama tool definition from a callable's signature.

    Works through `GuardedTool` because the wrapper keeps `__wrapped__`, so the
    schema describes the real tool rather than `(*args, **kwargs)`.
    """
    name = name or getattr(tool, "__name__", "tool")
    doc = inspect.getdoc(tool) or f"Call {name}."
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in inspect.signature(tool).parameters.items():
        if param_name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        properties[param_name] = {
            "type": _json_type(param.annotation),
            "description": f"{param_name} for {name}",
        }
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": doc.strip().splitlines()[0],
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _json_type(annotation: Any) -> str:
    mapping = {int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    if annotation in mapping:
        return mapping[annotation]
    text = str(annotation).lower()
    for key, value in (("int", "integer"), ("float", "number"), ("bool", "boolean"), ("list", "array")):
        if key in text:
            return value
    return "string"


def format_evidence(records: list[EvidenceRecord], *, max_chars: int = MAX_CHARS_PER_RECORD) -> str:
    """Render surviving records for the model.

    Says nothing about what was filtered. That's on purpose, see the module
    docstring.
    """
    if not records:
        return "(no results)"
    lines = []
    for i, record in enumerate(records, 1):
        stamp = record.published_at.date().isoformat() if record.published_at else "undated"
        body = record.content.strip()
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "..."
        lines.append(f"[{i}] {record.source_id} (published {stamp})\n{body}")
    return "\n\n".join(lines)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a model's reply.

    Small models wrap JSON in code fences, prose, or both. This scans for a
    balanced object rather than trusting the whole string to parse.
    """
    if not text:
        return None
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for end in range(start, len(text)):
            c = text[end]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : end + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    return None


class AgentRunner:
    """Runs a task against a model with a set of guarded tools."""

    def __init__(
        self,
        tools: Mapping[str, GuardedTool],
        client: OllamaClient | None = None,
    ) -> None:
        if not tools:
            raise ValueError("AgentRunner needs at least one tool")
        self.tools = dict(tools)
        self.client = client or OllamaClient()

    def run(self, config: AgentConfig) -> AgentRun:
        self._check_guards_agree(config.as_of)
        model = config.model or self.client.pick_model(prefer_tools=True)
        mode = self._resolve_mode(config.mode, model)
        run = AgentRun(config=config, model=model, mode=mode, audit=self._audit())
        if mode == "native":
            self._run_native(run)
        else:
            self._run_react(run)
        return run

    def _audit(self) -> AuditLog:
        """The tools' shared log, if they share one."""
        logs = {id(t.audit): t.audit for t in self.tools.values()}
        return next(iter(logs.values())) if len(logs) == 1 else AuditLog()

    def _check_guards_agree(self, as_of: datetime) -> None:
        """Catch the footgun where the prompt says one date and the filter uses another."""
        for name, tool in self.tools.items():
            if tool.guard.as_of != as_of:
                raise ValueError(
                    f"tool {name!r} is guarded at {tool.guard.as_of.isoformat()} but the run "
                    f"is configured for {as_of.isoformat()}. Build the tools with the same guard."
                )

    def _resolve_mode(self, mode: str, model: str) -> Literal["native", "react"]:
        if mode in ("native", "react"):
            return mode  # type: ignore[return-value]
        return "native" if self.client.supports_tools(model) else "react"

    def _system_prompt(self, config: AgentConfig, *, react: bool) -> str:
        as_of = config.as_of.date().isoformat()
        base = (
            f"The current date is {as_of}. You are answering as of that date and know "
            f"nothing that happened after it. Use your tools to gather evidence, and base "
            f"your answer only on what they return. If the evidence does not answer the "
            f"question, say so plainly rather than guessing."
        )
        if not react:
            return base
        listing = "\n".join(f"- {self._describe(name, tool)}" for name, tool in self.tools.items())
        return (
            f"{base}\n\n"
            f"Tools available:\n{listing}\n\n"
            "Reply with exactly one JSON object and nothing else.\n"
            'To use a tool: {"tool": "<name>", "arguments": {"query": "<search text>"}}\n'
            'To finish: {"answer": "<your answer>"}\n\n'
            "Search at least once before answering."
        )

    def _describe(self, name: str, tool: GuardedTool) -> str:
        params = ", ".join(
            p for p in inspect.signature(tool).parameters if p != "self"
        )
        doc = (inspect.getdoc(tool) or "").strip().splitlines()
        return f"{name}({params}): {doc[0] if doc else 'no description'}"

    def _call_tool(self, run: AgentRun, name: str, arguments: dict[str, Any]) -> str:
        """Run one guarded tool and fold the survivors into the run."""
        tool = self.tools.get(name)
        if tool is None:
            run.steps.append(
                AgentStep(kind="error", tool=name, text=f"no such tool: {name}")
            )
            return f"Error: no tool named {name}. Available: {', '.join(self.tools)}."

        try:
            records = tool(**arguments)
        except TypeError as exc:
            run.steps.append(AgentStep(kind="error", tool=name, text=str(exc)))
            return f"Error calling {name}: {exc}"

        result = tool.last_result
        self._collect(run, records)
        run.steps.append(
            AgentStep(
                kind="tool_call",
                tool=name,
                arguments=arguments,
                kept_count=len(records),
                filtered_count=result.filtered_count if result else 0,
            )
        )
        return format_evidence(records)

    def _collect(self, run: AgentRun, records: list[EvidenceRecord]) -> None:
        seen = {r.source_id for r in run.evidence}
        for record in records:
            if record.source_id not in seen:
                run.evidence.append(record)
                seen.add(record.source_id)

    def _run_native(self, run: AgentRun) -> None:
        schemas = [tool_schema(tool, name) for name, tool in self.tools.items()]
        messages = [
            ChatMessage(role="system", content=self._system_prompt(run.config, react=False)),
            ChatMessage(role="user", content=run.config.task),
        ]

        for _ in range(run.config.max_steps):
            response = self.client.chat(
                run.model, messages, tools=schemas, temperature=run.config.temperature
            )
            messages.append(response.message)

            if not response.tool_calls:
                run.final_answer = response.content.strip()
                run.steps.append(AgentStep(kind="answer", text=run.final_answer))
                run.stopped_because = "answered"
                return

            for call in response.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = extract_json_object(arguments) or {}
                observation = self._call_tool(run, name, arguments)
                messages.append(
                    ChatMessage(role="tool", content=observation, tool_name=name or None)
                )

        run.stopped_because = "max_steps"
        run.final_answer = run.final_answer or self._last_text(messages)

    def _run_react(self, run: AgentRun) -> None:
        messages = [
            ChatMessage(role="system", content=self._system_prompt(run.config, react=True)),
            ChatMessage(role="user", content=run.config.task),
        ]
        retries = 0

        for _ in range(run.config.max_steps):
            response = self.client.chat(run.model, messages, temperature=run.config.temperature)
            text = response.content.strip()
            messages.append(ChatMessage(role="assistant", content=text))
            action = extract_json_object(text)

            if action is None or not ({"tool", "answer"} & set(action)):
                if retries >= run.config.max_format_retries:
                    run.final_answer = text
                    run.steps.append(AgentStep(kind="answer", text=text))
                    run.stopped_because = "answered"
                    return
                retries += 1
                run.steps.append(AgentStep(kind="nudge", text=text))
                messages.append(
                    ChatMessage(
                        role="user",
                        content=(
                            "Reply with exactly one JSON object and nothing else, either "
                            '{"tool": "...", "arguments": {...}} or {"answer": "..."}.'
                        ),
                    )
                )
                continue

            if "answer" in action:
                run.final_answer = str(action["answer"]).strip()
                run.steps.append(AgentStep(kind="answer", text=run.final_answer))
                run.stopped_because = "answered"
                return

            name = str(action.get("tool") or "")
            arguments = action.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"query": str(arguments)}
            observation = self._call_tool(run, name, arguments)
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        f"Results from {name}:\n\n{observation}\n\n"
                        "Reply with one JSON object: another tool call, or your answer."
                    ),
                )
            )

        run.stopped_because = "max_steps"
        run.final_answer = run.final_answer or self._last_text(messages)

    @staticmethod
    def _last_text(messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == "assistant" and message.content.strip():
                return message.content.strip()
        return ""


def run_agent(
    config: AgentConfig,
    tools: Mapping[str, GuardedTool],
    *,
    client: OllamaClient | None = None,
) -> AgentRun:
    """Run one task. The front door for phase 3."""
    return AgentRunner(tools, client=client).run(config)
