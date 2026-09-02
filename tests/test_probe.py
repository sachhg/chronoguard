"""Tests for the parametric leakage probe.

Scoring is a pure function, so all of it runs offline against synthetic cases.
The model-in-the-loop parts use a canned client.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from chronoguard.probe import (
    CutoffRisk,
    LeakageProbe,
    ModelCutoffs,
    ProbeCase,
    ProbeOutcome,
    ProbeReport,
    exact_match,
    fuzzy_match,
    load_model_cutoffs,
    load_probe_cases,
    looks_like_refusal,
    normalize,
    score_response,
    squash,
)
from helpers import CannedProbeClient

UTC = timezone.utc
AS_OF = datetime(2023, 6, 1, tzinfo=UTC)


def case(
    case_id: str = "c1",
    answer: str = "Meridian",
    aliases: list[str] | None = None,
    knowable_from: str = "2024-01-01T00:00:00Z",
    question: str = "What is the answer?",
) -> ProbeCase:
    return ProbeCase(
        id=case_id,
        question=question,
        answer=answer,
        aliases=aliases or [],
        knowable_from=knowable_from,
    )


def outcome(
    kind: str = "future", revealed: bool = False, refused: bool = False, case_id: str = "c"
) -> ProbeOutcome:
    return ProbeOutcome(
        case_id=case_id,
        question="q",
        expected="a",
        kind=kind,
        knowable_from="2024-01-01T00:00:00Z",
        response="r",
        revealed=revealed,
        method="exact" if revealed else "none",
        refused=refused,
    )


def report(outcomes: list[ProbeOutcome], level: str = "unknown") -> ProbeReport:
    return ProbeReport(
        model="m",
        as_of=AS_OF,
        cutoff_risk=CutoffRisk(model="m", as_of=AS_OF, level=level),
        outcomes=outcomes,
    )


class TestNormalization:
    def test_squash_ignores_punctuation_and_case(self) -> None:
        assert squash("GPT-4") == squash("gpt 4") == squash("GPT4") == "gpt4"

    def test_squash_handles_currency(self) -> None:
        assert squash("$3,499") == "3499"

    def test_normalize_joins_digit_groups(self) -> None:
        assert "3499" in normalize("$3,499")

    def test_normalize_collapses_whitespace(self) -> None:
        assert normalize("  a   b  ") == "a b"

    def test_normalize_unifies_dashes(self) -> None:
        assert normalize("GPT‑4") == normalize("GPT-4")


class TestExactMatch:
    @pytest.mark.parametrize(
        "response",
        [
            "The answer is Meridian.",
            "meridian",
            "MERIDIAN!",
            "Probably Meridian, I think.",
        ],
    )
    def test_matches_regardless_of_case_and_punctuation(self, response: str) -> None:
        assert exact_match(response, ["Meridian"]).matched

    def test_matches_an_alias(self) -> None:
        result = exact_match("It was Hinton.", ["Geoffrey Hinton", "Hinton"])
        assert result.matched
        assert result.matched_text == "Hinton"

    def test_number_formats_agree(self) -> None:
        assert exact_match("Apple priced it at 3499 dollars", ["$3,499"]).matched
        assert exact_match("It cost $3,499.", ["$3,499"]).matched

    def test_hyphenation_does_not_matter(self) -> None:
        assert exact_match("They shipped GPT 4 in March", ["GPT-4"]).matched

    def test_no_match(self) -> None:
        result = exact_match("Something else entirely", ["Meridian"])
        assert not result.matched
        assert result.method == "none"

    def test_short_answers_need_a_token_boundary(self) -> None:
        # "X" must not match every word containing an x.
        assert not exact_match("The extra excellent example", ["X"]).matched
        assert exact_match("The answer is X.", ["X"]).matched

    def test_empty_variants_are_skipped(self) -> None:
        assert not exact_match("anything", ["", "  "]).matched


class TestFuzzyMatch:
    def test_catches_a_typo(self) -> None:
        result = fuzzy_match("I believe it was Geoffrey Hintno.", ["Geoffrey Hinton"])
        assert result.matched
        assert result.method == "fuzzy"

    def test_a_long_response_does_not_dilute_the_score(self) -> None:
        padding = "some preamble that goes on and on for quite a while indeed " * 5
        assert fuzzy_match(f"{padding} Geoffrey Hintan", ["Geoffrey Hinton"]).matched

    def test_threshold_is_respected(self) -> None:
        assert not fuzzy_match("Completely different words", ["Geoffrey Hinton"], 0.9).matched

    def test_a_lower_threshold_is_more_permissive(self) -> None:
        strict = fuzzy_match("Geoffry Hnton", ["Geoffrey Hinton"], 0.99)
        loose = fuzzy_match("Geoffry Hnton", ["Geoffrey Hinton"], 0.7)
        assert not strict.matched and loose.matched

    def test_score_is_reported_even_when_it_misses(self) -> None:
        assert 0 < fuzzy_match("Geoffry Hnton", ["Geoffrey Hinton"], 0.99).score < 1

    def test_empty_response(self) -> None:
        assert not fuzzy_match("", ["Meridian"]).matched


class TestRefusalDetection:
    @pytest.mark.parametrize(
        "response",
        ["I DO NOT KNOW", "I don't know.", "I have no information about that", "That is beyond my knowledge"],
    )
    def test_refusals_are_recognised(self, response: str) -> None:
        assert looks_like_refusal(response)

    def test_an_actual_answer_is_not_a_refusal(self) -> None:
        assert not looks_like_refusal("Sam Altman")


class TestScoreResponse:
    def test_exact_wins_first(self) -> None:
        assert score_response("It was Meridian", case()).method == "exact"

    def test_falls_through_to_fuzzy(self) -> None:
        assert score_response("It was Meridain", case()).method == "fuzzy"

    def test_judge_is_the_last_resort(self) -> None:
        calls: list[str] = []

        def judge(response: str, c: ProbeCase) -> bool:
            calls.append(response)
            return True

        result = score_response("the scheduling platform they announced", case(), judge=judge)
        assert result.method == "judge"
        assert calls

    def test_judge_is_not_consulted_when_matching_already_worked(self) -> None:
        calls: list[str] = []
        score_response("Meridian", case(), judge=lambda r, c: calls.append(r) or True)
        assert calls == []

    def test_judge_saying_no_leaves_it_unmatched(self) -> None:
        assert not score_response("no idea what that is", case(), judge=lambda r, c: False).matched

    def test_a_refusal_never_reaches_the_judge(self) -> None:
        calls: list[str] = []
        score_response("I DO NOT KNOW", case(), judge=lambda r, c: calls.append(r) or True)
        assert calls == []

    def test_empty_response_is_not_a_match(self) -> None:
        assert not score_response("   ", case()).matched

    def test_no_judge_configured_behaves_the_same_minus_the_fallback(self) -> None:
        assert not score_response("something vague", case()).matched


class TestProbeCase:
    def test_a_case_knowable_after_as_of_is_a_probe(self) -> None:
        assert case(knowable_from="2024-01-01T00:00:00Z").kind_for(AS_OF) == "future"

    def test_a_case_knowable_before_as_of_is_a_control(self) -> None:
        assert case(knowable_from="2020-01-01T00:00:00Z").kind_for(AS_OF) == "control"

    def test_the_boundary_instant_counts_as_future(self) -> None:
        # Same exclusive rule as the guard.
        assert case(knowable_from="2023-06-01T00:00:00Z").kind_for(AS_OF) == "future"

    def test_variants_include_the_answer_and_aliases(self) -> None:
        assert case(answer="A", aliases=["B"]).variants == ["A", "B"]


class TestModelCutoffs:
    @pytest.mark.parametrize(
        "name,family",
        [
            ("gemma3:4b", "gemma3"),
            ("llama3.2:3b-instruct-q4_0", "llama3.2"),
            ("library/qwen3:8b", "qwen3"),
            ("mistral", "mistral"),
            ("GEMMA3:4B", "gemma3"),
        ],
    )
    def test_family_extraction(self, name: str, family: str) -> None:
        assert ModelCutoffs.family_of(name) == family

    def test_exact_family_lookup(self) -> None:
        cutoffs = ModelCutoffs(cutoffs={"gemma3": date(2024, 8, 1)})
        assert cutoffs.lookup("gemma3:4b") == ("gemma3", date(2024, 8, 1))

    def test_longest_prefix_wins(self) -> None:
        cutoffs = ModelCutoffs(cutoffs={"llama3": date(2023, 3, 1), "llama3.1": date(2023, 12, 1)})
        assert cutoffs.lookup("llama3.1:8b")[0] == "llama3.1"

    def test_unknown_model_gives_nothing(self) -> None:
        assert ModelCutoffs(cutoffs={"gemma3": date(2024, 8, 1)}).lookup("something-else") is None


class TestCutoffRisk:
    def test_as_of_before_the_cutoff_is_flagged_high(self) -> None:
        cutoffs = ModelCutoffs(cutoffs={"gemma3": date(2024, 8, 1)})
        risk = CutoffRisk.assess("gemma3:4b", AS_OF, cutoffs)
        assert risk.level == "high"
        assert "2024-08-01" in risk.reason
        assert "cannot undo" in risk.reason

    def test_as_of_after_the_cutoff_is_low(self) -> None:
        cutoffs = ModelCutoffs(cutoffs={"gemma3": date(2022, 1, 1)})
        assert CutoffRisk.assess("gemma3:4b", AS_OF, cutoffs).level == "low"

    def test_a_model_with_no_entry_is_unknown_not_safe(self) -> None:
        risk = CutoffRisk.assess("mystery:7b", AS_OF, ModelCutoffs())
        assert risk.level == "unknown"
        assert "model_cutoffs.json" in risk.reason

    def test_the_matched_family_is_reported(self) -> None:
        cutoffs = ModelCutoffs(cutoffs={"gemma3": date(2024, 8, 1)})
        assert CutoffRisk.assess("gemma3:4b", AS_OF, cutoffs).matched_family == "gemma3"


class TestProbeReport:
    def test_leakage_score_counts_only_future_cases(self) -> None:
        result = report(
            [
                outcome("future", revealed=True),
                outcome("future", revealed=False),
                outcome("control", revealed=True),
            ]
        )
        assert result.leakage_score == 0.5
        assert result.control_score == 1.0

    def test_scores_are_zero_when_there_is_nothing_to_score(self) -> None:
        result = report([])
        assert result.leakage_score == 0.0
        assert result.control_score == 0.0
        assert result.risk_level == "inconclusive"

    def test_heavy_leakage_reads_high(self) -> None:
        result = report([outcome("future", revealed=True) for _ in range(3)])
        assert result.risk_level == "high"

    def test_any_leakage_reads_elevated(self) -> None:
        outcomes = [outcome("future", revealed=True)] + [outcome("future") for _ in range(4)]
        outcomes += [outcome("control", revealed=True) for _ in range(2)]
        result = report(outcomes)
        assert result.leakage_score == 0.2
        assert result.risk_level == "elevated"

    def test_clean_with_working_controls_reads_low(self) -> None:
        result = report(
            [outcome("future"), outcome("control", revealed=True), outcome("control", revealed=True)]
        )
        assert result.risk_level == "low"

    def test_a_model_that_fails_the_controls_is_inconclusive_not_clean(self) -> None:
        # The whole reason controls exist: zero leakage from a model that can't
        # answer anything is not evidence of blinding.
        result = report([outcome("future"), outcome("control"), outcome("control")])
        assert result.leakage_score == 0.0
        assert result.risk_level == "inconclusive"
        assert "can't answer" in result.explain()

    def test_refusal_rate_covers_future_cases(self) -> None:
        result = report([outcome("future", refused=True), outcome("future")])
        assert result.refusal_rate == 0.5

    def test_summary_carries_the_headline_numbers(self) -> None:
        text = report([outcome("future", revealed=True), outcome("control", revealed=True)]).summary()
        assert "leakage 1/1" in text and "control 1/1" in text and "risk high" in text

    def test_explain_names_the_leaked_cases(self) -> None:
        result = report([outcome("future", revealed=True, case_id="uk-pm-2024")])
        assert "uk-pm-2024" in result.explain()

    def test_report_serializes_to_json(self) -> None:
        assert "leaked" not in json.loads(report([outcome()]).model_dump_json())


class TestPackagedData:
    def test_probe_cases_load(self) -> None:
        cases = load_probe_cases()
        assert len(cases) >= 10
        assert all(c.knowable_from.tzinfo is not None for c in cases)

    def test_case_ids_are_unique(self) -> None:
        ids = [c.id for c in load_probe_cases()]
        assert len(ids) == len(set(ids))

    def test_the_set_spans_both_sides_of_a_mid_range_as_of(self) -> None:
        cases = load_probe_cases()
        kinds = {c.kind_for(AS_OF) for c in cases}
        assert kinds == {"future", "control"}, "the set needs probes and controls to be useful"

    def test_cutoffs_load(self) -> None:
        cutoffs = load_model_cutoffs()
        assert cutoffs.cutoffs
        assert all(isinstance(v, date) for v in cutoffs.cutoffs.values())

    def test_a_custom_case_file_can_be_used(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "mine",
                            "question": "q",
                            "answer": "a",
                            "knowable_from": "2024-01-01T00:00:00Z",
                        }
                    ]
                }
            )
        )
        assert [c.id for c in load_probe_cases(path)] == ["mine"]

    def test_a_bare_list_file_also_works(self, tmp_path: Path) -> None:
        path = tmp_path / "cases.json"
        path.write_text(
            json.dumps(
                [{"id": "mine", "question": "q", "answer": "a", "knowable_from": "2024-01-01T00:00:00Z"}]
            )
        )
        assert [c.id for c in load_probe_cases(path)] == ["mine"]

    def test_a_custom_cutoff_file_can_be_used(self, tmp_path: Path) -> None:
        path = tmp_path / "cutoffs.json"
        path.write_text(json.dumps({"cutoffs": {"mine": "2024-01-01"}}))
        assert load_model_cutoffs(path).lookup("mine:7b") == ("mine", date(2024, 1, 1))


class TestLeakageProbeRun:
    @pytest.fixture
    def cases(self) -> list[ProbeCase]:
        return [
            case("future-1", "Keir Starmer", question="Who won?", knowable_from="2024-07-05T00:00:00Z"),
            case("future-2", "DeepSeek", question="Which lab?", knowable_from="2025-01-20T00:00:00Z"),
            case("control-1", "Microsoft", question="Who bought GitHub?", knowable_from="2018-06-04T00:00:00Z"),
        ]

    def test_a_full_run_scores_both_groups(self, cases: list[ProbeCase]) -> None:
        client = CannedProbeClient({"Who won?": "Keir Starmer", "Who bought GitHub?": "Microsoft"})
        result = LeakageProbe(client, cases=cases, cutoffs=ModelCutoffs()).run("m", AS_OF)

        assert result.leakage_score == 0.5
        assert result.control_score == 1.0
        assert result.risk_level == "high"
        assert {o.case_id for o in result.leaked} == {"future-1"}

    def test_the_probe_never_mentions_the_as_of_date_to_the_model(
        self, cases: list[ProbeCase]
    ) -> None:
        # Telling it to pretend would measure instruction-following, not knowledge.
        client = CannedProbeClient()
        LeakageProbe(client, cases=cases, cutoffs=ModelCutoffs()).run("m", AS_OF)
        assert all("2023" not in q for q in client.asked)

    def test_refusals_are_recorded(self, cases: list[ProbeCase]) -> None:
        client = CannedProbeClient(default="I DO NOT KNOW")
        result = LeakageProbe(client, cases=cases, cutoffs=ModelCutoffs()).run("m", AS_OF)
        assert result.refusal_rate == 1.0
        assert result.leakage_score == 0.0

    def test_cutoff_risk_is_assessed_before_any_question_is_asked(
        self, cases: list[ProbeCase]
    ) -> None:
        cutoffs = ModelCutoffs(cutoffs={"gemma3": date(2024, 8, 1)})
        result = LeakageProbe(CannedProbeClient(), cases=cases, cutoffs=cutoffs).run("gemma3:4b", AS_OF)
        assert result.cutoff_risk.level == "high"

    def test_case_limits_keep_the_newest_cases(self, cases: list[ProbeCase]) -> None:
        result = LeakageProbe(CannedProbeClient(), cases=cases, cutoffs=ModelCutoffs()).run(
            "m", AS_OF, max_future_cases=1, max_control_cases=0
        )
        assert [o.case_id for o in result.outcomes] == ["future-2"]

    def test_the_judge_is_used_when_configured(self, cases: list[ProbeCase]) -> None:
        client = CannedProbeClient({"Who won?": "the Labour leader"}, judge_reply="YES")
        result = LeakageProbe(
            client, cases=cases, cutoffs=ModelCutoffs(), judge_model="judge"
        ).run("m", AS_OF)
        assert client.judged
        assert [o.method for o in result.outcomes if o.case_id == "future-1"] == ["judge"]

    def test_no_judge_means_no_judge_calls(self, cases: list[ProbeCase]) -> None:
        client = CannedProbeClient({"Who won?": "the Labour leader"})
        LeakageProbe(client, cases=cases, cutoffs=ModelCutoffs()).run("m", AS_OF)
        assert client.judged == []

    def test_a_naive_as_of_is_refused(self, cases: list[ProbeCase]) -> None:
        probe = LeakageProbe(CannedProbeClient(), cases=cases, cutoffs=ModelCutoffs())
        with pytest.raises(ValueError, match="explicit offset"):
            probe.run("m", "2023-06-01")

    def test_an_as_of_after_every_case_leaves_nothing_to_probe(
        self, cases: list[ProbeCase]
    ) -> None:
        result = LeakageProbe(CannedProbeClient(), cases=cases, cutoffs=ModelCutoffs()).run(
            "m", "2030-01-01T00:00:00Z"
        )
        assert result.future_outcomes == []
        assert result.risk_level == "inconclusive"
