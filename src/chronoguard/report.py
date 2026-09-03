"""End-to-end scenario reporting.

Chains the three measurements into one artefact:

1. **Agent run** (containment). What the guard let through, what it dropped.
2. **Leakage probe** (measurement). What the model already knew, no tools.
3. **Claim classification** (measurement). What the answer asserted that the
   evidence never supplied.

Each on its own is easy to misread. A clean agent run means the filter worked,
not that the agent was blinded. A high probe score means the model knows the
period, not that this particular answer used that knowledge. A flagged claim
means something specific came from somewhere other than the evidence. Put the
three together and you can actually say how much to trust the run, which is
what `headline_risk` does and why it carries its reasons around with it.

Two outputs, same data: `render()` for a human, `summary()` for a machine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from chronoguard._version import __version__
from chronoguard.agent import AgentConfig, AgentRun, run_agent
from chronoguard.claims import ClaimReport, classify_run
from chronoguard.guard import GuardPolicy, TemporalGuard
from chronoguard.interception import AuditLog, GuardedTool
from chronoguard.ollama import OllamaClient
from chronoguard.probe import LeakageProbe, ProbeReport

__all__ = ["ScenarioConfig", "ScenarioReport", "run_scenario"]

RiskLevel = Literal["high", "elevated", "low", "unknown"]


class ScenarioConfig(BaseModel):
    """Everything one end-to-end run needs."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    task: str
    as_of: AwareDatetime
    model: str | None = None
    judge_model: str | None = Field(
        default=None, description="Model for claim classification. Reuses the agent model if unset."
    )
    mode: Literal["auto", "native", "react"] = "auto"
    policy: GuardPolicy = GuardPolicy.STRICT
    max_steps: int = 6
    probe: bool = True
    max_future_cases: int | None = None
    max_control_cases: int | None = None
    classify: bool = True
    max_claims: int = 8


class ScenarioReport(BaseModel):
    """One run, all three measurements, both output shapes."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    config: ScenarioConfig
    agent: AgentRun
    probe: ProbeReport | None = None
    claims: ClaimReport | None = None
    generated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    chronoguard_version: str = __version__

    # Containment

    @property
    def audit(self) -> AuditLog:
        return self.agent.audit

    @property
    def records_filtered(self) -> int:
        return self.audit.filtered_count

    @property
    def records_kept(self) -> int:
        return self.audit.kept_count

    # Headline

    @property
    def headline_risk(self) -> RiskLevel:
        """How much to trust this run as a point-in-time simulation."""
        return self._headline()[0]

    @property
    def headline_reasons(self) -> list[str]:
        return self._headline()[1]

    def _headline(self) -> tuple[RiskLevel, list[str]]:
        reasons: list[str] = []
        level: RiskLevel = "low"

        def raise_to(new: RiskLevel) -> None:
            nonlocal level
            order = {"low": 0, "unknown": 1, "elevated": 2, "high": 3}
            if order[new] > order[level]:
                level = new

        if self.claims and self.claims.leaks:
            raise_to("high")
            reasons.append(
                f"the answer asserts {len(self.claims.leaks)} specific fact(s) that the "
                "evidence it received does not contain"
            )

        if self.probe:
            if self.probe.risk_level == "high":
                raise_to("high")
                reasons.append(
                    f"with no tools at all the model reproduced {self.probe.leakage_score:.0%} "
                    "of the post-as-of facts it was asked about"
                )
            elif self.probe.risk_level == "elevated":
                raise_to("elevated")
                reasons.append(
                    f"the model reproduced {len(self.probe.leaked)} post-as-of fact(s) with no "
                    "evidence in context"
                )
            elif self.probe.risk_level == "inconclusive":
                raise_to("unknown")
                reasons.append(
                    "the probe was inconclusive: the model also failed the control questions, "
                    "so its clean leakage score means it cannot answer, not that it is blinded"
                )
            if self.probe.cutoff_risk.level == "high":
                raise_to("elevated")
                reasons.append(
                    f"the model's training data runs past the simulated date "
                    f"({self.probe.cutoff_risk.known_cutoff}), so filtering cannot blind it"
                )
        else:
            raise_to("unknown")
            reasons.append("no leakage probe was run, so parametric leakage is unmeasured")

        if not self.claims:
            raise_to("unknown")
            reasons.append("the answer was not classified, so its claims are unchecked")

        if level == "low":
            reasons.append(
                "the filter held, the model showed no knowledge of the period, and every "
                "factual claim traced back to the evidence"
            )
        return level, reasons

    # Machine readable

    def summary(self) -> dict[str, Any]:
        """The machine-readable summary. Stable shape, safe to diff across runs."""
        payload: dict[str, Any] = {
            "chronoguard_version": self.chronoguard_version,
            "generated_at": self.generated_at.isoformat(),
            "as_of": self.config.as_of.isoformat(),
            "task": self.config.task,
            "model": self.agent.model,
            "mode": self.agent.mode,
            "policy": self.config.policy.value,
            "headline": {"risk": self.headline_risk, "reasons": self.headline_reasons},
            "tool_leakage": {
                "tool_calls": len(self.agent.tool_calls),
                "records_seen": self.audit.total,
                "records_kept": self.records_kept,
                "records_filtered": self.records_filtered,
                "verdicts": self.audit.counts,
                "by_tool": self.audit.by_tool(),
            },
            "parametric_leakage": None,
            "claims": None,
            "answer": self.agent.final_answer,
            "evidence": [
                {
                    "source_id": record.source_id,
                    "published_at": record.published_at.isoformat() if record.published_at else None,
                }
                for record in self.agent.evidence
            ],
        }

        if self.probe:
            payload["parametric_leakage"] = {
                "leakage_score": self.probe.leakage_score,
                "control_score": self.probe.control_score,
                "risk_level": self.probe.risk_level,
                "refusal_rate": self.probe.refusal_rate,
                "questions_asked": len(self.probe.outcomes),
                "cutoff_risk": {
                    "level": self.probe.cutoff_risk.level,
                    "known_cutoff": (
                        self.probe.cutoff_risk.known_cutoff.isoformat()
                        if self.probe.cutoff_risk.known_cutoff
                        else None
                    ),
                    "reason": self.probe.cutoff_risk.reason,
                },
                "leaked_cases": [
                    {
                        "case_id": o.case_id,
                        "expected": o.expected,
                        "response": o.response,
                        "matched_by": o.method,
                    }
                    for o in self.probe.leaked
                ],
            }

        if self.claims:
            payload["claims"] = {
                "judge_model": self.claims.judge_model,
                "total": len(self.claims.claims),
                "counts": self.claims.counts,
                "groundedness": self.claims.groundedness,
                "flagged": [
                    {
                        "claim": c.text,
                        "label": c.label.value,
                        "reason": c.reason,
                        "evidence_ids": c.evidence_ids,
                    }
                    for c in self.claims.leaks + self.claims.unclassified
                ],
            }
        return payload

    # Human readable

    def render(self) -> str:
        """The human-readable report."""
        lines = [
            "ChronoGuard report",
            f"  as of    {self.config.as_of.isoformat()}  (policy: {self.config.policy.value})",
            f"  model    {self.agent.model} [{self.agent.mode}]",
            f"  task     {self.config.task}",
            "",
            f"RISK: {self.headline_risk.upper()}",
        ]
        lines += [f"  - {reason}" for reason in self.headline_reasons]
        lines += ["", *self._render_containment()]
        lines += ["", *self._render_probe()]
        lines += ["", *self._render_claims()]
        lines += ["", "ANSWER", *_indent(self.agent.final_answer or "(no answer)")]
        lines += ["", *self._render_evidence()]
        return "\n".join(lines)

    def _render_containment(self) -> list[str]:
        counts = ", ".join(f"{k}={v}" for k, v in self.audit.counts.items() if v)
        lines = [
            "TOOL LEAKAGE (contained by filtering)",
            f"  {len(self.agent.tool_calls)} tool call(s), {self.audit.total} record(s) retrieved",
            f"  kept {self.records_kept}, filtered {self.records_filtered}"
            + (f"  ({counts})" if counts else ""),
        ]
        for name, row in self.audit.by_tool().items():
            lines.append(
                f"    {name:<18} {row['total']:>3} seen, {row['kept']:>3} kept, "
                f"{row['filtered']:>3} filtered"
            )
        if self.config.policy is GuardPolicy.WARN:
            lines.append("  warn policy: violations were flagged, not withheld")
        return lines

    def _render_probe(self) -> list[str]:
        if not self.probe:
            return ["PARAMETRIC LEAKAGE (measured, not contained)", "  not measured, probe skipped"]
        probe = self.probe
        lines = [
            "PARAMETRIC LEAKAGE (measured, not contained)",
            f"  leakage {len(probe.leaked)}/{len(probe.future_outcomes)} "
            f"({probe.leakage_score:.0%}), control "
            f"{sum(1 for o in probe.control_outcomes if o.revealed)}/{len(probe.control_outcomes)} "
            f"({probe.control_score:.0%}), risk {probe.risk_level}",
            f"  cutoff risk: {probe.cutoff_risk.level}",
            *_indent(probe.cutoff_risk.reason, width=4),
        ]
        if probe.leaked:
            lines.append("  produced with zero evidence in context:")
            for outcome in probe.leaked:
                lines.append(f"    {outcome.case_id:<22} expected {outcome.expected!r}")
        return lines

    def _render_claims(self) -> list[str]:
        if not self.claims:
            return ["CLAIMS IN THE ANSWER", "  not classified, step skipped"]
        claims = self.claims
        lines = [
            "CLAIMS IN THE ANSWER",
            f"  {len(claims.claims)} claim(s): {len(claims.grounded)} grounded, "
            f"{len(claims.benign)} benign, {claims.leak_count} suspected leak(s), "
            f"groundedness {claims.groundedness:.0%}",
        ]
        flagged = claims.leaks + claims.unclassified
        if flagged:
            lines.append("  flagged:")
            for claim in flagged:
                lines.append(f"    [{claim.label.value}] {claim.text}")
                lines += _indent(claim.reason, width=6)
        return lines

    def _render_evidence(self) -> list[str]:
        lines = [f"EVIDENCE THE AGENT RECEIVED ({len(self.agent.evidence)})"]
        if not self.agent.evidence:
            lines.append("  none, every retrieved record was withheld")
            return lines
        for record in self.agent.evidence:
            stamp = record.published_at.date().isoformat() if record.published_at else "undated"
            lines.append(f"  {stamp}  {record.source_id}")
        return lines


def _indent(text: str, width: int = 2) -> list[str]:
    pad = " " * width
    return [f"{pad}{line}" for line in text.strip().splitlines()] or [f"{pad}"]


def run_scenario(
    config: ScenarioConfig,
    tools: dict[str, GuardedTool] | None = None,
    *,
    client: OllamaClient | None = None,
) -> ScenarioReport:
    """Run the agent, then the probe, then the classifier, and bundle the lot.

    Tools default to the packaged fixture toolset, guarded at `config.as_of`.
    """
    client = client or OllamaClient()
    if tools is None:
        from chronoguard.fixtures import build_fixture_toolset

        guard = TemporalGuard(config.as_of, policy=config.policy)
        tools = build_fixture_toolset(guard, AuditLog())

    agent_run = run_agent(
        AgentConfig(
            task=config.task,
            as_of=config.as_of,
            model=config.model,
            mode=config.mode,
            max_steps=config.max_steps,
        ),
        tools,
        client=client,
    )

    probe_report = None
    if config.probe:
        probe_report = LeakageProbe(client).run(
            agent_run.model,
            config.as_of,
            max_future_cases=config.max_future_cases,
            max_control_cases=config.max_control_cases,
        )

    claim_report = None
    if config.classify:
        claim_report = classify_run(
            agent_run,
            client=client,
            judge_model=config.judge_model or agent_run.model,
            max_claims=config.max_claims,
        )

    return ScenarioReport(
        config=config, agent=agent_run, probe=probe_report, claims=claim_report
    )
