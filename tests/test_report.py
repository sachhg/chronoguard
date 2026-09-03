"""Tests for end-to-end scenario reporting.

Two things under test: the headline verdict logic, which is where the three
measurements get combined into an actual judgement, and the two output shapes.
All offline.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from chronoguard.agent import AgentConfig, AgentRun, AgentStep
from chronoguard.claims import Claim, ClaimLabel, ClaimReport
from chronoguard.evidence import EvidenceRecord
from chronoguard.guard import GuardPolicy, TemporalGuard
from chronoguard.interception import AuditLog
from chronoguard.probe import CutoffRisk, ProbeOutcome, ProbeReport
from chronoguard.report import ScenarioConfig, ScenarioReport, run_scenario
from chronoguard.fixtures import FIXTURE_AS_OF, POST_AS_OF_CANARIES, build_fixture_toolset
from helpers import ScenarioClient, action, answer

UTC = timezone.utc
AS_OF = datetime(2023, 6, 1, tzinfo=UTC)
TASK = "When will Meridian ship?"

EVIDENCE = [
    EvidenceRecord.from_source("Halden confirms a summer window.", "doc-1", published_at="2023-05-31T00:00:00Z"),
    EvidenceRecord.from_source("Analysts guess under $3,000.", "doc-2", published_at="2023-03-22T00:00:00Z"),
]


def agent_run(
    *,
    answer_text: str = "Summer, no price announced.",
    evidence: list[EvidenceRecord] | None = None,
    audit: AuditLog | None = None,
) -> AgentRun:
    return AgentRun(
        config=AgentConfig(task=TASK, as_of=AS_OF, model="m"),
        model="m",
        mode="react",
        steps=[AgentStep(kind="tool_call", tool="web_search", kept_count=2, filtered_count=3)],
        final_answer=answer_text,
        evidence=EVIDENCE if evidence is None else evidence,
        audit=audit or AuditLog(),
    )


def probe_report(
    *,
    leaked: int = 0,
    future: int = 4,
    controls_right: int = 3,
    controls: int = 3,
    cutoff: str = "low",
) -> ProbeReport:
    outcomes = [
        ProbeOutcome(
            case_id=f"future-{i}",
            question="q",
            expected="a",
            kind="future",
            knowable_from="2024-01-01T00:00:00Z",
            response="r",
            revealed=i < leaked,
            method="exact" if i < leaked else "none",
        )
        for i in range(future)
    ] + [
        ProbeOutcome(
            case_id=f"control-{i}",
            question="q",
            expected="a",
            kind="control",
            knowable_from="2018-01-01T00:00:00Z",
            response="r",
            revealed=i < controls_right,
            method="exact" if i < controls_right else "none",
        )
        for i in range(controls)
    ]
    risk = CutoffRisk(
        model="m",
        as_of=AS_OF,
        level=cutoff,
        known_cutoff=date(2024, 8, 1) if cutoff != "unknown" else None,
        reason="cutoff reason text",
    )
    return ProbeReport(model="m", as_of=AS_OF, cutoff_risk=risk, outcomes=outcomes)


def claim_report(*, leaks: int = 0, grounded: int = 2, benign: int = 1, unclassified: int = 0) -> ClaimReport:
    claims = (
        [Claim(text=f"grounded {i}", label=ClaimLabel.GROUNDED, evidence_ids=["doc-1"]) for i in range(grounded)]
        + [Claim(text=f"benign {i}", label=ClaimLabel.BENIGN) for i in range(benign)]
        + [
            Claim(text=f"Ferrous Labs bought them, item {i}.", label=ClaimLabel.LEAK, reason="in no document")
            for i in range(leaks)
        ]
        + [Claim(text=f"unclear {i}", label=ClaimLabel.UNCLASSIFIED, reason="judge said ???") for i in range(unclassified)]
    )
    return ClaimReport(answer="a", judge_model="judge", claims=claims, evidence_count=2)


def scenario(
    *,
    probe: ProbeReport | None = None,
    claims: ClaimReport | None = None,
    policy: GuardPolicy = GuardPolicy.STRICT,
    run: AgentRun | None = None,
) -> ScenarioReport:
    return ScenarioReport(
        config=ScenarioConfig(task=TASK, as_of=AS_OF, policy=policy),
        agent=run or agent_run(),
        probe=probe,
        claims=claims,
    )


class TestHeadlineRisk:
    def test_everything_clean_reads_low(self) -> None:
        result = scenario(probe=probe_report(), claims=claim_report())
        assert result.headline_risk == "low"
        assert any("filter held" in r for r in result.headline_reasons)

    def test_a_leaked_claim_makes_it_high(self) -> None:
        result = scenario(probe=probe_report(), claims=claim_report(leaks=1))
        assert result.headline_risk == "high"
        assert any("does not contain" in r for r in result.headline_reasons)

    def test_heavy_probe_leakage_makes_it_high(self) -> None:
        result = scenario(probe=probe_report(leaked=3, future=4), claims=claim_report())
        assert result.headline_risk == "high"
        assert any("no tools at all" in r for r in result.headline_reasons)

    def test_some_probe_leakage_makes_it_elevated(self) -> None:
        result = scenario(probe=probe_report(leaked=1, future=5), claims=claim_report())
        assert result.headline_risk == "elevated"

    def test_a_cutoff_past_the_as_of_date_makes_it_elevated_on_its_own(self) -> None:
        # Even with a spotless run: the model demonstrably read past the moment
        # being simulated, so the run cannot be called clean.
        result = scenario(probe=probe_report(cutoff="high"), claims=claim_report())
        assert result.headline_risk == "elevated"
        assert any("filtering cannot blind it" in r for r in result.headline_reasons)

    def test_failed_controls_make_it_unknown_not_low(self) -> None:
        result = scenario(probe=probe_report(controls_right=0), claims=claim_report())
        assert result.headline_risk == "unknown"
        assert any("cannot answer" in r for r in result.headline_reasons)

    def test_skipping_the_probe_makes_it_unknown(self) -> None:
        result = scenario(probe=None, claims=claim_report())
        assert result.headline_risk == "unknown"
        assert any("unmeasured" in r for r in result.headline_reasons)

    def test_skipping_classification_makes_it_unknown(self) -> None:
        result = scenario(probe=probe_report(), claims=None)
        assert result.headline_risk == "unknown"
        assert any("unchecked" in r for r in result.headline_reasons)

    def test_the_worst_signal_wins(self) -> None:
        # A leaked claim outranks an otherwise clean probe.
        result = scenario(probe=probe_report(), claims=claim_report(leaks=1))
        assert result.headline_risk == "high"

    def test_reasons_accumulate_rather_than_replacing_each_other(self) -> None:
        result = scenario(probe=probe_report(leaked=3, future=4, cutoff="high"), claims=claim_report(leaks=1))
        assert len(result.headline_reasons) >= 3


class TestJsonSummary:
    def test_the_three_required_numbers_are_present(self) -> None:
        payload = scenario(probe=probe_report(leaked=1, future=4), claims=claim_report(leaks=1)).summary()
        assert payload["tool_leakage"]["records_filtered"] == 0
        assert payload["parametric_leakage"]["leakage_score"] == 0.25
        assert payload["claims"]["flagged"][0]["reason"] == "in no document"

    def test_filtered_counts_come_from_the_audit_log(self) -> None:
        guard = TemporalGuard(FIXTURE_AS_OF)
        audit = AuditLog()
        tools = build_fixture_toolset(guard, audit)
        tools["web_search"]("meridian price", limit=99)
        payload = scenario(run=agent_run(audit=audit)).summary()

        assert payload["tool_leakage"]["records_filtered"] > 0
        assert payload["tool_leakage"]["records_seen"] == audit.total
        assert payload["tool_leakage"]["by_tool"]["web_search"]["filtered"] > 0

    def test_skipped_stages_are_null_not_missing(self) -> None:
        payload = scenario().summary()
        assert payload["parametric_leakage"] is None
        assert payload["claims"] is None

    def test_flagged_claims_carry_their_reasons(self) -> None:
        payload = scenario(claims=claim_report(leaks=2, unclassified=1)).summary()
        flagged = payload["claims"]["flagged"]
        assert len(flagged) == 3
        assert all(item["reason"] for item in flagged)
        assert {item["label"] for item in flagged} == {"suspected-parametric-leak", "unclassified"}

    def test_leaked_probe_cases_are_listed(self) -> None:
        payload = scenario(probe=probe_report(leaked=2, future=4)).summary()
        assert len(payload["parametric_leakage"]["leaked_cases"]) == 2
        assert payload["parametric_leakage"]["cutoff_risk"]["reason"] == "cutoff reason text"

    def test_evidence_is_listed_with_dates(self) -> None:
        payload = scenario().summary()
        assert [e["source_id"] for e in payload["evidence"]] == ["doc-1", "doc-2"]
        assert payload["evidence"][0]["published_at"].startswith("2023-05-31")

    def test_the_headline_is_in_the_summary(self) -> None:
        payload = scenario(probe=probe_report(), claims=claim_report(leaks=1)).summary()
        assert payload["headline"]["risk"] == "high"
        assert payload["headline"]["reasons"]

    def test_the_whole_summary_is_json_serializable(self) -> None:
        payload = scenario(probe=probe_report(leaked=1), claims=claim_report(leaks=1)).summary()
        assert json.loads(json.dumps(payload))["chronoguard_version"]

    def test_version_and_timestamps_are_recorded(self) -> None:
        payload = scenario().summary()
        assert payload["chronoguard_version"]
        assert payload["generated_at"]
        assert payload["as_of"].startswith("2023-06-01")


class TestTextReport:
    def test_every_section_is_present(self) -> None:
        text = scenario(probe=probe_report(), claims=claim_report()).render()
        for heading in (
            "ChronoGuard report",
            "RISK:",
            "TOOL LEAKAGE",
            "PARAMETRIC LEAKAGE",
            "CLAIMS IN THE ANSWER",
            "ANSWER",
            "EVIDENCE THE AGENT RECEIVED",
        ):
            assert heading in text

    def test_the_headline_leads_with_its_reasons(self) -> None:
        text = scenario(probe=probe_report(), claims=claim_report(leaks=1)).render()
        assert "RISK: HIGH" in text
        assert "does not contain" in text

    def test_flagged_claims_are_shown_with_reasons(self) -> None:
        text = scenario(claims=claim_report(leaks=1)).render()
        assert "Ferrous Labs bought them" in text
        assert "in no document" in text

    def test_a_clean_answer_shows_no_flagged_section(self) -> None:
        assert "flagged:" not in scenario(claims=claim_report()).render()

    def test_skipped_stages_say_so(self) -> None:
        text = scenario().render()
        assert "probe skipped" in text
        assert "step skipped" in text

    def test_warn_policy_is_called_out(self) -> None:
        assert "not withheld" in scenario(policy=GuardPolicy.WARN).render()

    def test_an_empty_answer_is_labelled(self) -> None:
        assert "(no answer)" in scenario(run=agent_run(answer_text="")).render()

    def test_a_run_with_no_surviving_evidence_says_so(self) -> None:
        text = scenario(run=agent_run(evidence=[])).render()
        assert "every retrieved record was withheld" in text

    def test_probe_leaks_are_named(self) -> None:
        assert "future-0" in scenario(probe=probe_report(leaked=1)).render()

    def test_the_report_is_plain_text_with_no_stray_markup(self) -> None:
        text = scenario(probe=probe_report(), claims=claim_report()).render()
        assert "\t" not in text
        assert not text.startswith("\n")


class TestRunScenario:
    def _client(self, **kwargs: object) -> ScenarioClient:
        return ScenarioClient(
            [action("web_search", query="meridian price"), answer("Summer, no price yet.")],
            probe_answers={"Nobel Peace Prize": "Narges Mohammadi"},
            claims=["Halden confirmed a summer window.", "No price has been announced."],
            **kwargs,
        )

    def test_all_three_stages_run(self) -> None:
        client = self._client()
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, max_future_cases=2, max_control_cases=1),
            client=client,
        )
        assert set(client.stages) == {"agent", "probe", "judge"}
        assert result.agent.final_answer == "Summer, no price yet."
        assert result.probe is not None
        assert result.claims is not None

    def test_the_default_tools_are_guarded_at_the_configured_as_of(self) -> None:
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, probe=False, classify=False),
            client=self._client(),
        )
        assert result.agent.audit.filtered_count > 0
        leaked = [c for c in POST_AS_OF_CANARIES if c in result.agent.evidence_text]
        assert leaked == []

    def test_the_probe_can_be_skipped(self) -> None:
        client = self._client()
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, probe=False), client=client
        )
        assert result.probe is None
        assert "probe" not in client.stages

    def test_classification_can_be_skipped(self) -> None:
        client = self._client()
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, classify=False, max_future_cases=1, max_control_cases=1),
            client=client,
        )
        assert result.claims is None
        assert "judge" not in client.stages

    def test_the_warn_policy_reaches_the_guard(self) -> None:
        result = run_scenario(
            ScenarioConfig(
                task=TASK, as_of=FIXTURE_AS_OF, policy=GuardPolicy.WARN, probe=False, classify=False
            ),
            client=self._client(),
        )
        assert result.agent.audit.filtered_count == 0
        assert result.agent.audit.violation_count > 0

    def test_custom_tools_are_used_when_given(self) -> None:
        guard = TemporalGuard(FIXTURE_AS_OF)
        tools = build_fixture_toolset(guard, AuditLog())
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, probe=False, classify=False),
            tools,
            client=self._client(),
        )
        assert result.agent.audit is tools["web_search"].audit

    def test_the_judge_defaults_to_the_agent_model(self) -> None:
        result = run_scenario(
            ScenarioConfig(task=TASK, as_of=FIXTURE_AS_OF, probe=False), client=self._client()
        )
        assert result.claims is not None
        assert result.claims.judge_model == result.agent.model

    def test_a_naive_as_of_is_refused_by_the_config(self) -> None:
        with pytest.raises(Exception):
            ScenarioConfig(task=TASK, as_of="2023-06-01")
