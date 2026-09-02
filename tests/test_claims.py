"""Tests for claim-level leakage classification.

The parsers and the aggregation are pure, so they're tested directly. The
model-in-the-loop path uses a scripted judge, which checks the plumbing: that
the evidence reaches the prompt, that document numbers map back to source ids,
and that verdicts land on the right claims. Whether a real judge model gets the
labels right is measured in the integration suite against the same fixtures.
"""

from __future__ import annotations

import pytest

from chronoguard.agent import AgentConfig, AgentRun
from chronoguard.claims import (
    Claim,
    ClaimClassifier,
    ClaimLabel,
    ClaimReport,
    classify_run,
    parse_claims,
    parse_verdict,
)
from chronoguard.evidence import EvidenceRecord
from claim_fixtures import ANSWER, EVIDENCE, LABELLED_CLAIMS
from helpers import ScriptedJudgeClient

GROUNDED = "GROUNDED | 1 | document 1 says so"
BENIGN = "BENIGN | - | a prediction"
LEAK = "UNSUPPORTED | - | appears in no document"


def claim(text: str = "c", label: ClaimLabel = ClaimLabel.GROUNDED) -> Claim:
    return Claim(text=text, label=label)


def report(claims: list[Claim], evidence_count: int = 3) -> ClaimReport:
    return ClaimReport(
        answer="a", judge_model="m", claims=claims, evidence_count=evidence_count
    )


class TestParseClaims:
    def test_plain_lines(self) -> None:
        assert parse_claims("First claim here.\nSecond claim here.") == [
            "First claim here.",
            "Second claim here.",
        ]

    @pytest.mark.parametrize("marker", ["- ", "* ", "• ", "1. ", "2) ", "  - "])
    def test_list_markers_are_stripped(self, marker: str) -> None:
        assert parse_claims(f"{marker}The thing happened.") == ["The thing happened."]

    def test_preamble_is_dropped(self) -> None:
        blob = "Here are the claims:\nThe thing happened.\nSure, hope that helps"
        assert parse_claims(blob) == ["The thing happened."]

    def test_headers_ending_in_a_colon_are_dropped(self) -> None:
        assert parse_claims("Claims:\nThe thing happened.") == ["The thing happened."]

    def test_single_word_lines_are_dropped(self) -> None:
        assert parse_claims("Okay\nThe thing happened.") == ["The thing happened."]

    def test_duplicates_are_collapsed(self) -> None:
        assert parse_claims("Same claim.\nSame claim.") == ["Same claim."]

    def test_surrounding_quotes_are_stripped(self) -> None:
        assert parse_claims('"The thing happened."') == ["The thing happened."]

    def test_max_claims_is_respected(self) -> None:
        blob = "\n".join(f"Claim number {i} here." for i in range(20))
        assert len(parse_claims(blob, max_claims=5)) == 5

    def test_empty_input(self) -> None:
        assert parse_claims("") == []
        assert parse_claims("\n\n   \n") == []


class TestParseVerdict:
    @pytest.mark.parametrize(
        "reply,expected",
        [
            (GROUNDED, ClaimLabel.GROUNDED),
            ("SUPPORTED | 1 | yes", ClaimLabel.GROUNDED),
            (BENIGN, ClaimLabel.BENIGN),
            ("REASONING | - | speculation", ClaimLabel.BENIGN),
            (LEAK, ClaimLabel.LEAK),
            ("MISSING | - | nope", ClaimLabel.LEAK),
        ],
    )
    def test_label_words(self, reply: str, expected: ClaimLabel) -> None:
        assert parse_verdict(reply)[0] is expected

    def test_a_label_buried_in_prose_is_still_found(self) -> None:
        assert parse_verdict("I think this one is GROUNDED because doc 1 says it")[0] is ClaimLabel.GROUNDED

    def test_the_earliest_label_word_wins(self) -> None:
        # "BENIGN, not UNSUPPORTED" must read as benign.
        assert parse_verdict("BENIGN | - | it is a hedge, not UNSUPPORTED")[0] is ClaimLabel.BENIGN

    def test_document_numbers_are_extracted(self) -> None:
        assert parse_verdict("GROUNDED | 1, 3 | both say it")[1] == ["1", "3"]

    def test_a_dash_means_no_documents(self) -> None:
        assert parse_verdict(LEAK)[1] == []

    def test_the_reason_is_kept(self) -> None:
        assert parse_verdict(GROUNDED)[2] == "document 1 says so"

    def test_missing_pipes_still_parse(self) -> None:
        label, sources, reason = parse_verdict("GROUNDED")
        assert label is ClaimLabel.GROUNDED and sources == [] and reason == "GROUNDED"

    def test_garbage_is_unclassified_not_guessed(self) -> None:
        assert parse_verdict("wat")[0] is ClaimLabel.UNCLASSIFIED

    def test_empty_reply_is_unclassified(self) -> None:
        label, _, reason = parse_verdict("   ")
        assert label is ClaimLabel.UNCLASSIFIED
        assert "nothing" in reason


class TestClaimReport:
    def test_counts_by_label(self) -> None:
        result = report(
            [
                claim(label=ClaimLabel.GROUNDED),
                claim(label=ClaimLabel.LEAK),
                claim(label=ClaimLabel.BENIGN),
            ]
        )
        assert result.counts == {
            "grounded": 1,
            "ungrounded-but-benign": 1,
            "suspected-parametric-leak": 1,
            "unclassified": 0,
        }

    def test_groundedness_ignores_benign_claims(self) -> None:
        # An answer that is mostly hedging isn't well grounded, it just isn't
        # asserting much, so benign claims stay out of the denominator.
        result = report(
            [claim(label=ClaimLabel.GROUNDED), claim(label=ClaimLabel.LEAK)]
            + [claim(label=ClaimLabel.BENIGN) for _ in range(8)]
        )
        assert result.groundedness == 0.5

    def test_groundedness_with_no_factual_claims(self) -> None:
        assert report([claim(label=ClaimLabel.BENIGN)]).groundedness == 1.0

    def test_empty_report(self) -> None:
        result = report([])
        assert result.claims == []
        assert result.leak_count == 0
        assert result.groundedness == 1.0

    def test_leaks_are_listed(self) -> None:
        result = report([claim("the leak", ClaimLabel.LEAK), claim("fine", ClaimLabel.GROUNDED)])
        assert [c.text for c in result.leaks] == ["the leak"]
        assert result.leaks[0].is_leak

    def test_unclassified_claims_are_surfaced_not_hidden(self) -> None:
        result = report([claim(label=ClaimLabel.UNCLASSIFIED)])
        assert len(result.unclassified) == 1
        assert "could not label" in result.explain()

    def test_summary_carries_the_numbers(self) -> None:
        text = report([claim(label=ClaimLabel.GROUNDED), claim(label=ClaimLabel.LEAK)]).summary()
        assert "1 grounded" in text and "1 suspected leak" in text

    def test_explain_names_the_leaked_claims(self) -> None:
        result = report([claim("Ferrous Labs bought them.", ClaimLabel.LEAK)])
        assert "Ferrous Labs" in result.explain()

    def test_report_serializes(self) -> None:
        assert "grounded" in report([claim()]).model_dump_json()


class TestClaimClassifier:
    def test_end_to_end_with_a_scripted_judge(self) -> None:
        texts = [t for t, _, _ in LABELLED_CLAIMS]
        client = ScriptedJudgeClient(
            claims=texts,
            verdicts={
                "October 14": LEAK,
                "Ferrous Labs": LEAK,
                "probably": BENIGN,
                "does not name": BENIGN,
            },
            default_verdict=GROUNDED,
        )
        result = ClaimClassifier(client, "judge").classify(ANSWER, EVIDENCE)

        assert [c.text for c in result.claims] == texts
        assert result.counts["grounded"] == 2
        assert result.counts["suspected-parametric-leak"] == 2
        assert result.counts["ungrounded-but-benign"] == 2
        assert result.groundedness == 0.5

    def test_the_evidence_reaches_the_classify_prompt(self) -> None:
        client = ScriptedJudgeClient(claims=["A claim here."])
        ClaimClassifier(client, "judge").classify("answer", EVIDENCE)
        prompt = client.classify_prompts[0]
        assert "summer launch window" in prompt
        assert "[1]" in prompt and "[3]" in prompt

    def test_document_numbers_map_back_to_source_ids(self) -> None:
        client = ScriptedJudgeClient(
            claims=["A claim here."], default_verdict="GROUNDED | 2 | doc 2"
        )
        result = ClaimClassifier(client, "judge").classify("answer", EVIDENCE)
        assert result.claims[0].evidence_ids == [EVIDENCE[1].source_id]

    def test_out_of_range_document_numbers_are_dropped(self) -> None:
        client = ScriptedJudgeClient(
            claims=["A claim here."], default_verdict="GROUNDED | 9 | doc 9"
        )
        result = ClaimClassifier(client, "judge").classify("answer", EVIDENCE)
        assert result.claims[0].evidence_ids == []

    def test_with_no_evidence_the_prompt_says_so(self) -> None:
        client = ScriptedJudgeClient(claims=["A claim here."])
        result = ClaimClassifier(client, "judge").classify("answer", [])
        assert "(no documents were provided)" in client.classify_prompts[0]
        assert result.evidence_count == 0

    def test_every_specific_claim_leaks_when_there_was_no_evidence(self) -> None:
        client = ScriptedJudgeClient(claims=["It shipped in October."], default_verdict=LEAK)
        result = ClaimClassifier(client, "judge").classify("answer", [])
        assert result.leak_count == 1

    def test_an_empty_answer_produces_nothing(self) -> None:
        client = ScriptedJudgeClient(claims=["should not be asked"])
        result = ClaimClassifier(client, "judge").classify("   ", EVIDENCE)
        assert result.claims == []
        assert client.prompts == []

    def test_a_judge_that_will_not_decompose_falls_back_to_one_claim(self) -> None:
        client = ScriptedJudgeClient(decompose_reply="", default_verdict=GROUNDED)
        result = ClaimClassifier(client, "judge").classify("A single sentence answer.", EVIDENCE)
        assert [c.text for c in result.claims] == ["A single sentence answer."]

    def test_max_claims_caps_the_judge_calls(self) -> None:
        client = ScriptedJudgeClient(claims=[f"Claim number {i} here." for i in range(20)])
        result = ClaimClassifier(client, "judge", max_claims=3).classify("answer", EVIDENCE)
        assert len(result.claims) == 3
        assert len(client.classify_prompts) == 3

    def test_the_raw_verdict_is_kept_for_auditing(self) -> None:
        client = ScriptedJudgeClient(claims=["A claim here."], default_verdict=GROUNDED)
        result = ClaimClassifier(client, "judge").classify("answer", EVIDENCE)
        assert result.claims[0].raw_verdict == GROUNDED

    def test_the_judge_model_is_discovered_when_unset(self) -> None:
        client = ScriptedJudgeClient(claims=["A claim here."])
        assert ClaimClassifier(client).classify("answer", EVIDENCE).judge_model == "scripted-judge"

    def test_an_unusable_verdict_lands_as_unclassified(self) -> None:
        client = ScriptedJudgeClient(claims=["A claim here."], default_verdict="???")
        result = ClaimClassifier(client, "judge").classify("answer", EVIDENCE)
        assert result.claims[0].label is ClaimLabel.UNCLASSIFIED


class TestClassifyRun:
    def test_an_agent_run_is_classified_against_the_evidence_it_received(self) -> None:
        run = AgentRun(
            config=AgentConfig(task="t", as_of="2023-06-01T00:00:00Z"),
            model="m",
            mode="react",
            final_answer="Meridian will ship on October 14 at $4,900 per seat.",
            evidence=EVIDENCE,
        )
        client = ScriptedJudgeClient(claims=[run.final_answer], default_verdict=LEAK)
        result = classify_run(run, client=client, judge_model="judge")

        assert result.leak_count == 1
        assert result.evidence_count == len(EVIDENCE)

    def test_a_run_that_retrieved_nothing_still_classifies(self) -> None:
        run = AgentRun(
            config=AgentConfig(task="t", as_of="2023-06-01T00:00:00Z"),
            model="m",
            mode="react",
            final_answer="It shipped in October.",
            evidence=[],
        )
        client = ScriptedJudgeClient(claims=[run.final_answer], default_verdict=LEAK)
        assert classify_run(run, client=client, judge_model="judge").leak_count == 1
