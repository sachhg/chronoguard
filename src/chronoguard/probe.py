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
    "CaseIssue",
    "CaseSetReport",
    "CutoffRisk",
    "LeakageProbe",
    "ModelCutoffs",
    "ProbeCase",
    "ProbeOutcome",
    "ProbeReport",
    "exact_match",
    "fuzzy_match",
    "load_model_cutoffs",
    "describe_cases",
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
    """Lowercase, drop thousands separators, collapse the rest to spaces.

    Only commas come out of numbers. Stripping spaces too would merge
    "17 2023" into "172023", making a date's two numbers look like one and
    defeating the exact-digit rule in fuzzy_match.
    """
    text = text.lower()
    text = re.sub(r"[‐-―]", "-", text)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
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


def _digit_runs(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def fuzzy_match(
    response: str, variants: list[str], threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> MatchOutcome:
    """Best sliding-window similarity between the response and any variant.

    Slides a window the length of the expected answer across the response, so a
    long reply doesn't dilute the score the way whole-string similarity would.

    Numbers inside a variant are matched exactly, never fuzzily. "November 17"
    against "November 13" scores 0.91 on characters and is a different fact, and
    the same trap catches prices, quantities and model version numbers. If a
    variant carries digits, every one of its digit runs has to appear in the
    window before the ratio is even considered.
    """
    tokens = normalize(response).split()
    best_score = 0.0
    best_text: str | None = None

    for variant in variants:
        expected = normalize(variant)
        if not expected:
            continue
        required = _digit_runs(expected)
        width = len(expected.split())
        windows = [
            " ".join(tokens[i : i + width]) for i in range(max(len(tokens) - width + 1, 1))
        ]
        for window in windows or [""]:
            if required and not required <= _digit_runs(window):
                continue
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


class CaseIssue(BaseModel):
    """Something wrong, or suspicious, about a case set."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning", "note"]
    case_id: str | None = None
    message: str

    def render(self) -> str:
        mark = {"error": "ERROR  ", "warning": "WARNING", "note": "note   "}[self.severity]
        where = f"[{self.case_id}] " if self.case_id else ""
        return f"  {mark}  {where}{self.message}"


class CaseSetReport(BaseModel):
    """What a case set looks like at a given as-of, and what's wrong with it.

    Two jobs. The coverage half answers "will the probe tell me anything at this
    date", which matters because a set with no future cases scores zero leakage
    and reads like a blinded model. The validation half catches the mistakes
    that quietly corrupt a score.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    as_of: AwareDatetime
    total: int
    future: int
    control: int
    topics: dict[str, int] = Field(default_factory=dict)
    nearest_future: list[str] = Field(default_factory=list)
    issues: list[CaseIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[CaseIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def usable(self) -> bool:
        """Whether a probe at this as-of could produce a meaningful number."""
        return not self.errors and self.future > 0 and self.control > 0

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": self.source,
            "as_of": self.as_of.isoformat(),
            "total": self.total,
            "future": self.future,
            "control": self.control,
            "usable": self.usable,
            "topics": self.topics,
            "nearest_future": self.nearest_future,
            "issues": [
                {"severity": i.severity, "case_id": i.case_id, "message": i.message}
                for i in self.issues
            ],
        }

    def render(self) -> str:
        topics = ", ".join(f"{name}={n}" for name, n in sorted(self.topics.items()))
        lines = [
            self.source,
            f"  as of    {self.as_of.isoformat()}",
            f"  cases    {self.total}",
            f"  future   {self.future}  (leakage questions at this date)",
            f"  control  {self.control}  (already knowable, so a floor on ability)",
        ]
        if topics:
            lines.append(f"  topics   {topics}")
        if self.nearest_future:
            lines.append(f"  nearest  {', '.join(self.nearest_future)}")
        if self.issues:
            lines.append("")
            lines += [i.render() for i in self.issues]
        return "\n".join(lines)


def describe_cases(
    cases: list[ProbeCase], as_of: datetime | str, *, source: str = "packaged set"
) -> CaseSetReport:
    """Describe and validate a case set at a given as-of."""
    moment = parse_timestamp(as_of)
    if moment is None:
        raise ValueError(
            f"as_of must be a timezone-aware instant, got {as_of!r}. "
            "Add an explicit offset, for example '2023-06-01T00:00:00Z'."
        )

    future = [c for c in cases if c.kind_for(moment) == "future"]
    control = [c for c in cases if c.kind_for(moment) == "control"]
    topics: dict[str, int] = {}
    for case in cases:
        topics[case.topic] = topics.get(case.topic, 0) + 1

    report = CaseSetReport(
        source=source,
        as_of=moment,
        total=len(cases),
        future=len(future),
        control=len(control),
        topics=topics,
        # Nearest first, matching how a capped run selects them. See
        # docs/kb/capped-probe-runs-take-nearest-cases.md.
        nearest_future=[c.id for c in sorted(future, key=lambda c: c.knowable_from)[:3]],
    )
    report.issues = _case_issues(cases, report)
    return report


def _case_issues(cases: list[ProbeCase], report: CaseSetReport) -> list[CaseIssue]:
    issues: list[CaseIssue] = []

    if not cases:
        return [CaseIssue(severity="error", message="the case set is empty")]

    seen: dict[str, int] = {}
    for case in cases:
        seen[case.id] = seen.get(case.id, 0) + 1
    for case_id, n in sorted(seen.items()):
        if n > 1:
            issues.append(
                CaseIssue(
                    severity="error",
                    case_id=case_id,
                    message=f"id appears {n} times; ids must be unique or scores double-count",
                )
            )

    for case in cases:
        if not case.answer.strip():
            issues.append(CaseIssue(severity="error", case_id=case.id, message="empty answer"))
        if not case.question.strip():
            issues.append(CaseIssue(severity="error", case_id=case.id, message="empty question"))

    if report.future == 0:
        issues.append(
            CaseIssue(
                severity="error",
                message=(
                    f"no case is in the future at {report.as_of.date()}, so the probe would "
                    "score 0/0 and the run would read as blinded when nothing was measured"
                ),
            )
        )
    elif report.future < 3:
        issues.append(
            CaseIssue(
                severity="warning",
                message=f"only {report.future} future case(s) at this date, so the score is coarse",
            )
        )

    if report.control == 0:
        issues.append(
            CaseIssue(
                severity="error",
                message=(
                    "no control cases at this date; without them a zero score means the model "
                    "cannot answer, not that it is blinded"
                ),
            )
        )

    for case in cases:
        leaked = _giveaways(case)
        if leaked:
            issues.append(
                CaseIssue(
                    severity="warning",
                    case_id=case.id,
                    message=(
                        f"the question already spells out {', '.join(repr(w) for w in leaked)}, "
                        "so any model could answer it from the question alone"
                    ),
                )
            )

    issues.append(
        CaseIssue(
            severity="note",
            message=(
                "only verbatim giveaways are checked. A question can still hand over its "
                "answer by description, and no automated check catches that. Ask whether a "
                "model knowing nothing after the cutoff could still answer it."
            ),
        )
    )
    return issues


def _giveaways(case: ProbeCase) -> list[str]:
    """Variants the question already spells out in full.

    Every token has to be present, not just one, and none are filtered out by
    length. Partial overlap is the normal case and flagging it buries the real
    ones: "on what date was he removed" shares "november" and "2023" with the
    answer "17 November 2023", but the discriminating "17" is absent, so the
    question gives nothing away. Dropping short tokens as noise would throw away
    exactly the one that decides it.

    Tokens are squashed the same way answers are matched, so "GPT-4" in a
    question counts against an answer of "gpt4" and a trailing full stop doesn't
    hide a name.
    """
    question = {squash(w) for w in normalize(case.question).split()}
    question.discard("")
    out = []
    for variant in case.variants:
        words = [squash(w) for w in normalize(variant).split()]
        words = [w for w in words if w]
        if words and all(word in question for word in words) and variant not in out:
            out.append(variant)
    return out


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
        """Order the cases so that capping a run keeps the informative ones.

        Future cases run nearest-to-as_of first. Those are the ones most likely
        to sit inside the model's training window, so they're where leakage
        actually shows up. Ordering the other way round makes a capped run ask
        only about things past every plausible cutoff and report a reassuring
        zero, which is exactly the wrong answer.

        Controls run most-recent-first, because a control the model barely
        predates is a more demanding check than one from 1969.
        """
        by_date = sorted(self.cases, key=lambda c: c.knowable_from)
        future = [c for c in by_date if c.kind_for(as_of) == "future"]
        control = [c for c in reversed(by_date) if c.kind_for(as_of) == "control"]
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
