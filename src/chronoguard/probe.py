"""Parametric leakage probe.

Layer 2, the half that filtering can't touch. The guard controls what comes in
through tools. This measures what the model already knew before the run started.

The method is blunt on purpose: ask the model questions whose answers only
became knowable after the as-of date, give it no tools at all, and count how
many it gets right. A correct answer with zero evidence in context came from
the weights. That's the whole idea.

Three things this module is careful about:

**It does not tell the model to pretend it's a past date.** That would measure
instruction-following, not knowledge. We want the model to try its hardest, so
the probe asks the question straight. What we're measuring is capability, not
compliance.

**It doesn't trust self-reported cutoffs.** Models are frequently wrong about
their own cutoff in both directions, and post-training on recent data blurs it.
The cutoff file is a prior that decides whether a run gets flagged before
scoring; the probe results are the evidence.

**It scores controls too.** Cases already knowable at the as-of date are run as
a control group. A model that scores zero on the future *and* zero on the
controls isn't well blinded, it just can't answer questions. Without the
control, those two look identical in the numbers.

Every case is both, depending on when you point it: `knowable_from >= as_of`
makes it a leakage probe, `knowable_from < as_of` makes it a control. Same
boundary rule as the guard, an answer knowable at exactly the as-of instant
counts as future.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from chronoguard.evidence import parse_timestamp
from chronoguard.ollama import OllamaClient

__all__ = [
    "CutoffRisk",
    "LeakageProbe",
    "ModelCutoffs",
    "ProbeCase",
    "ProbeOutcome",
    "ProbeReport",
    "exact_match",
    "fuzzy_match",
    "load_model_cutoffs",
    "load_probe_cases",
    "normalize",
    "score_response",
    "squash",
]

DEFAULT_FUZZY_THRESHOLD = 0.85
MIN_SQUASH_LENGTH = 3

REFUSAL_MARKERS = (
    "i do not know",
    "i don't know",
    "i dont know",
    "no information",
    "not aware of",
    "cannot answer",
    "can't answer",
    "unable to answer",
    "beyond my knowledge",
    "after my training",
    "no knowledge of",
)


def normalize(text: str) -> str:
    """Lowercase, drop separators inside numbers, collapse the rest to spaces."""
    text = text.lower()
    text = re.sub(r"[‐-―]", "-", text)
    text = re.sub(r"(?<=\d)[, \s](?=\d)", "", text)
    text = re.sub(r"[^a-z0-9.\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def squash(text: str) -> str:
    """Strip everything but letters and digits, so `GPT-4`, `GPT 4` and `gpt4` agree."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


class ProbeCase(BaseModel):
    """One question whose answer became knowable at a specific moment."""

    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    answer: str
    knowable_from: AwareDatetime
    aliases: list[str] = Field(default_factory=list)
    topic: str = "general"

    @property
    def variants(self) -> list[str]:
        """Everything that counts as revealing the answer."""
        return [self.answer, *self.aliases]

    def kind_for(self, as_of: datetime) -> Literal["future", "control"]:
        """Whether this is a leakage probe or a control at the given as-of."""
        return "future" if self.knowable_from >= as_of else "control"


class MatchOutcome(BaseModel):
    """How a response scored against a case."""

    model_config = ConfigDict(extra="forbid")

    matched: bool
    method: Literal["exact", "fuzzy", "judge", "none"]
    score: float = 0.0
    matched_text: str | None = None


def exact_match(response: str, variants: list[str]) -> MatchOutcome:
    """Squashed substring match. Catches formatting differences, not typos.

    Variants shorter than three squashed characters are matched on token
    boundaries instead, so a one-letter answer doesn't match every word
    containing that letter.
    """
    squashed_response = squash(response)
    squashed_tokens = {squash(token) for token in response.split()}
    for variant in variants:
        needle = squash(variant)
        if not needle:
            continue
        # Long answers can match anywhere. Short ones have to be their own word,
        # or a one-letter answer would match every word containing that letter.
        haystack = squashed_response if len(needle) >= MIN_SQUASH_LENGTH else squashed_tokens
        if needle in haystack:
            return MatchOutcome(matched=True, method="exact", score=1.0, matched_text=variant)
    return MatchOutcome(matched=False, method="none")


def fuzzy_match(
    response: str, variants: list[str], threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> MatchOutcome:
    """Best sliding-window similarity between the response and any variant.

    Slides a window the length of the expected answer across the response, so a
    long reply doesn't dilute the score the way whole-string similarity would.
    """
    tokens = normalize(response).split()
    best_score = 0.0
    best_text: str | None = None

    for variant in variants:
        expected = normalize(variant)
        if not expected:
            continue
        width = len(expected.split())
        windows = [
            " ".join(tokens[i : i + width]) for i in range(max(len(tokens) - width + 1, 1))
        ]
        for window in windows or [""]:
            score = SequenceMatcher(None, expected, window).ratio()
            if score > best_score:
                best_score, best_text = score, variant

    if best_score >= threshold:
        return MatchOutcome(matched=True, method="fuzzy", score=best_score, matched_text=best_text)
    return MatchOutcome(matched=False, method="none", score=best_score)


def looks_like_refusal(response: str) -> bool:
    """Whether the model said it doesn't know. The behaviour we actually want."""
    lowered = response.lower()
    return any(marker in lowered for marker in REFUSAL_MARKERS)


def score_response(
    response: str,
    case: ProbeCase,
    *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    judge: Any = None,
) -> MatchOutcome:
    """Decide whether a response reveals the case's answer.

    Exact first, then fuzzy, then an optional LLM judge for free-text answers
    that neither catches. The judge is only consulted when the cheap paths
    fail, so a run without one behaves identically apart from the last resort.
    """
    if not response.strip():
        return MatchOutcome(matched=False, method="none")

    outcome = exact_match(response, case.variants)
    if outcome.matched:
        return outcome

    outcome = fuzzy_match(response, case.variants, threshold)
    if outcome.matched:
        return outcome

    if judge is not None and not looks_like_refusal(response):
        if judge(response, case):
            return MatchOutcome(matched=True, method="judge", score=1.0, matched_text=case.answer)

    return outcome


def load_probe_cases(path: str | Path | None = None) -> list[ProbeCase]:
    """Load the packaged case set, or your own file in the same format."""
    if path is None:
        blob = resources.files("chronoguard.data").joinpath("probe_cases.json").read_text("utf-8")
    else:
        blob = Path(path).read_text(encoding="utf-8")
    payload = json.loads(blob)
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return [ProbeCase.model_validate(c) for c in cases]


class ModelCutoffs(BaseModel):
    """Approximate training cutoffs by model family.

    A prior, not evidence. Vendors are vague, post-training blurs the line, and
    models misreport their own cutoff in both directions. This only decides
    whether a run is flagged as high risk before any scoring happens.
    """

    model_config = ConfigDict(extra="forbid")

    cutoffs: dict[str, date] = Field(default_factory=dict)

    @staticmethod
    def family_of(model: str) -> str:
        """`library/llama3.2:3b-instruct-q4_0` becomes `llama3.2`."""
        return model.split("/")[-1].split(":")[0].strip().lower()

    def lookup(self, model: str) -> tuple[str, date] | None:
        """Exact family match, then the longest key the family starts with."""
        family = self.family_of(model)
        if family in self.cutoffs:
            return family, self.cutoffs[family]
        candidates = [k for k in self.cutoffs if family.startswith(k)]
        if not candidates:
            return None
        key = max(candidates, key=len)
        return key, self.cutoffs[key]


def load_model_cutoffs(path: str | Path | None = None) -> ModelCutoffs:
    """Load the packaged cutoff table, or your own."""
    if path is None:
        blob = resources.files("chronoguard.data").joinpath("model_cutoffs.json").read_text("utf-8")
    else:
        blob = Path(path).read_text(encoding="utf-8")
    payload = json.loads(blob)
    return ModelCutoffs(cutoffs=payload.get("cutoffs", payload) if isinstance(payload, dict) else {})


class CutoffRisk(BaseModel):
    """Whether the model's own training window already sinks the experiment."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str
    as_of: AwareDatetime
    known_cutoff: date | None = None
    matched_family: str | None = None
    level: Literal["high", "low", "unknown"] = "unknown"
    reason: str = ""

    @classmethod
    def assess(cls, model: str, as_of: datetime, cutoffs: ModelCutoffs) -> CutoffRisk:
        found = cutoffs.lookup(model)
        if found is None:
            return cls(
                model=model,
                as_of=as_of,
                level="unknown",
                reason=(
                    f"no training cutoff on file for {model!r}. Add one to "
                    "model_cutoffs.json, and treat the probe score as the only evidence."
                ),
            )
        family, cutoff = found
        if as_of.date() < cutoff:
            return cls(
                model=model,
                as_of=as_of,
                known_cutoff=cutoff,
                matched_family=family,
                level="high",
                reason=(
                    f"{model} was trained on data up to about {cutoff.isoformat()}, which is "
                    f"after the simulated date {as_of.date().isoformat()}. The model has read "
                    "past the moment you are trying to reconstruct. Filtering cannot undo that."
                ),
            )
        return cls(
            model=model,
            as_of=as_of,
            known_cutoff=cutoff,
            matched_family=family,
            level="low",
            reason=(
                f"{model}'s approximate cutoff of {cutoff.isoformat()} predates the simulated "
                f"date {as_of.date().isoformat()}, so its weights should not contain the answer. "
                "Approximate, so still worth probing."
            ),
        )


class ProbeOutcome(BaseModel):
    """One question asked, one answer scored."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str
    expected: str
    kind: Literal["future", "control"]
    knowable_from: AwareDatetime
    response: str
    revealed: bool
    method: Literal["exact", "fuzzy", "judge", "none"]
    score: float = 0.0
    refused: bool = False


class ProbeReport(BaseModel):
    """A leakage score for one (model, as_of) pair."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str
    as_of: AwareDatetime
    cutoff_risk: CutoffRisk
    outcomes: list[ProbeOutcome] = Field(default_factory=list)

    @property
    def future_outcomes(self) -> list[ProbeOutcome]:
        return [o for o in self.outcomes if o.kind == "future"]

    @property
    def control_outcomes(self) -> list[ProbeOutcome]:
        return [o for o in self.outcomes if o.kind == "control"]

    @property
    def leaked(self) -> list[ProbeOutcome]:
        return [o for o in self.future_outcomes if o.revealed]

    @property
    def leakage_score(self) -> float:
        """Share of post-as-of facts the model produced with no evidence at all."""
        future = self.future_outcomes
        return len(self.leaked) / len(future) if future else 0.0

    @property
    def control_score(self) -> float:
        """Share of already-knowable facts it got right. Sanity check on the model."""
        controls = self.control_outcomes
        if not controls:
            return 0.0
        return sum(1 for o in controls if o.revealed) / len(controls)

    @property
    def refusal_rate(self) -> float:
        future = self.future_outcomes
        return sum(1 for o in future if o.refused) / len(future) if future else 0.0

    @property
    def risk_level(self) -> Literal["high", "elevated", "low", "inconclusive"]:
        """The headline. Reads the control score so a useless model isn't called clean."""
        if not self.future_outcomes:
            return "inconclusive"
        if self.leakage_score >= 0.5:
            return "high"
        if self.leakage_score > 0:
            return "elevated"
        if self.control_outcomes and self.control_score < 0.5:
            return "inconclusive"
        return "low"

    def summary(self) -> str:
        future, controls = self.future_outcomes, self.control_outcomes
        return (
            f"{self.model} at {self.as_of.date().isoformat()}: "
            f"leakage {len(self.leaked)}/{len(future)} ({self.leakage_score:.0%}), "
            f"control {sum(1 for o in controls if o.revealed)}/{len(controls)} "
            f"({self.control_score:.0%}), risk {self.risk_level}, "
            f"cutoff risk {self.cutoff_risk.level}"
        )

    def explain(self) -> str:
        """A few lines a human can act on."""
        lines = [self.summary(), "", self.cutoff_risk.reason]
        if self.leaked:
            lines.append("")
            lines.append("Answered with no evidence in context:")
            for outcome in self.leaked:
                lines.append(
                    f"  {outcome.case_id}: expected {outcome.expected!r}, "
                    f"matched by {outcome.method}"
                )
        if self.risk_level == "inconclusive" and self.future_outcomes:
            lines.append("")
            lines.append(
                "The model also failed most of the control questions, so a zero leakage "
                "score here means it can't answer, not that it's blinded."
            )
        return "\n".join(lines)


class LeakageProbe:
    """Asks a model post-as-of questions with no tools and scores the answers.

    Args:
        client: Ollama client, or anything with a compatible `chat`.
        cases: Case set. Defaults to the packaged one.
        cutoffs: Cutoff table. Defaults to the packaged one.
        judge_model: Model to use as an LLM judge for free-text answers that
            exact and fuzzy matching both miss. Off by default.
        threshold: Fuzzy match cutoff.
        temperature: Kept at zero so a probe run is reproducible.
    """

    SYSTEM_PROMPT = (
        "Answer the question as directly as you can, in one short sentence. "
        "Give the specific name, number or date asked for. "
        "If you genuinely do not know, reply exactly: I DO NOT KNOW."
    )

    JUDGE_PROMPT = (
        "A question was asked and someone gave an answer. Decide whether their answer "
        "states that the correct answer is {expected!r}.\n\n"
        "Question: {question}\n"
        "Their answer: {response}\n\n"
        "Reply with exactly one word, YES or NO."
    )

    def __init__(
        self,
        client: OllamaClient | None = None,
        *,
        cases: list[ProbeCase] | None = None,
        cutoffs: ModelCutoffs | None = None,
        judge_model: str | None = None,
        threshold: float = DEFAULT_FUZZY_THRESHOLD,
        temperature: float = 0.0,
    ) -> None:
        self.client = client or OllamaClient()
        self.cases = cases if cases is not None else load_probe_cases()
        self.cutoffs = cutoffs if cutoffs is not None else load_model_cutoffs()
        self.judge_model = judge_model
        self.threshold = threshold
        self.temperature = temperature

    def run(
        self,
        model: str,
        as_of: datetime | str,
        *,
        max_future_cases: int | None = None,
        max_control_cases: int | None = None,
    ) -> ProbeReport:
        """Probe one model at one as-of date."""
        moment = parse_timestamp(as_of)
        if moment is None:
            raise ValueError(
                f"as_of must be a timezone-aware instant, got {as_of!r}. "
                "Add an explicit offset, for example '2023-06-01T00:00:00Z'."
            )

        risk = CutoffRisk.assess(model, moment, self.cutoffs)
        report = ProbeReport(model=model, as_of=moment, cutoff_risk=risk)

        for case in self._select(moment, max_future_cases, max_control_cases):
            report.outcomes.append(self._ask(model, case, moment))
        return report

    def _select(
        self, as_of: datetime, max_future: int | None, max_control: int | None
    ) -> list[ProbeCase]:
        """Newest future cases and newest controls, so limits keep the hardest ones."""
        ordered = sorted(self.cases, key=lambda c: c.knowable_from, reverse=True)
        future = [c for c in ordered if c.kind_for(as_of) == "future"]
        control = [c for c in ordered if c.kind_for(as_of) == "control"]
        return (future if max_future is None else future[:max_future]) + (
            control if max_control is None else control[:max_control]
        )

    def _ask(self, model: str, case: ProbeCase, as_of: datetime) -> ProbeOutcome:
        response = self.client.chat(
            model,
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": case.question},
            ],
            temperature=self.temperature,
        ).content.strip()

        outcome = score_response(
            response, case, threshold=self.threshold, judge=self._judge if self.judge_model else None
        )
        return ProbeOutcome(
            case_id=case.id,
            question=case.question,
            expected=case.answer,
            kind=case.kind_for(as_of),
            knowable_from=case.knowable_from,
            response=response,
            revealed=outcome.matched,
            method=outcome.method,
            score=outcome.score,
            refused=looks_like_refusal(response),
        )

    def _judge(self, response: str, case: ProbeCase) -> bool:
        """Last-resort LLM judge. Any non-YES reply counts as no match."""
        prompt = self.JUDGE_PROMPT.format(
            expected=case.answer, question=case.question, response=response
        )
        try:
            verdict = self.client.chat(
                self.judge_model or "",
                [{"role": "user", "content": prompt}],
                temperature=0.0,
            ).content
        except Exception:
            return False
        return verdict.strip().upper().startswith("YES")
