"""Claim-level leakage classification.

The bridge between the two layers. The guard controls what goes in; the probe
measures what the model knew beforehand. This looks at what came out.

Given an agent's final answer and the evidence it actually received after
filtering, it splits the answer into atomic claims and labels each one:

* **grounded**: the provided evidence states or directly supports it.
* **ungrounded-but-benign**: reasoning, hedging, opinion, or general background
  knowledge. Not a specific factual assertion, so nothing to leak.
* **suspected-parametric-leak**: a specific fact (a name, number, date, event)
  that isn't in the evidence and isn't general background. The observable
  fingerprint of parametric leakage in an end-to-end run.

That last label is the whole reason this module exists. Tool leakage shows up in
the audit log as a count of dropped records. Parametric leakage leaves no trace
at all in the plumbing: the agent asks for evidence, gets clean evidence, and
then writes down something it already knew. The only place to catch it is the
output.

One thing about the prompt. The judge is never asked "is this parametric
leakage?" Answering that would mean speculating about a model's internals.
It is asked the observable question, "is this specific fact present in the
evidence?", and this module applies the loaded name to the answer. Keeping the
judge's job narrow is what makes small local models usable for it.

Two model calls per answer plus one per claim, deliberately. A single call
returning a JSON array of labelled claims is cheaper and falls apart on 4B
models. Small focused calls with a one-line reply hold up.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chronoguard.agent import AgentRun, format_evidence
from chronoguard.evidence import EvidenceRecord
from chronoguard.ollama import OllamaClient

__all__ = [
    "Claim",
    "ClaimClassifier",
    "ClaimLabel",
    "ClaimReport",
    "classify_run",
    "parse_claims",
    "parse_verdict",
]

DEFAULT_MAX_CLAIMS = 12

#: Lines a model emits around a list rather than as part of it. Kept narrow:
#: "Claim" and "Answer" are ordinary ways to start a real sentence, so matching
#: on those drops genuine claims.
_PREAMBLE = re.compile(
    r"^(here (are|is)|these are|below|the following|sure|okay|ok|certainly)\b",
    re.IGNORECASE,
)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•–]|\d+[.)])\s*")


class ClaimLabel(str, Enum):
    """What one claim turned out to be."""

    GROUNDED = "grounded"
    BENIGN = "ungrounded-but-benign"
    LEAK = "suspected-parametric-leak"
    UNCLASSIFIED = "unclassified"
    """The judge replied with something unusable. Reported, never silently
    folded into one of the others."""


#: What the judge is asked to say, mapped to what we call it.
_VERDICT_WORDS = {
    "GROUNDED": ClaimLabel.GROUNDED,
    "SUPPORTED": ClaimLabel.GROUNDED,
    "BENIGN": ClaimLabel.BENIGN,
    "REASONING": ClaimLabel.BENIGN,
    "UNSUPPORTED": ClaimLabel.LEAK,
    "MISSING": ClaimLabel.LEAK,
}


def parse_claims(text: str, *, max_claims: int = DEFAULT_MAX_CLAIMS) -> list[str]:
    """Pull a list of claims out of a model's reply.

    Strips bullets and numbering, drops headers and chatter, keeps the order.
    """
    claims: list[str] = []
    for line in text.splitlines():
        cleaned = _LIST_MARKER.sub("", line.strip()).strip()
        cleaned = cleaned.strip('"').strip()
        if not cleaned or cleaned.endswith(":"):
            continue
        if _PREAMBLE.match(cleaned) and len(cleaned.split()) <= 8:
            continue
        if len(cleaned.split()) < 2:
            continue
        if cleaned not in claims:
            claims.append(cleaned)
        if len(claims) >= max_claims:
            break
    return claims


def parse_verdict(text: str) -> tuple[ClaimLabel, list[str], str]:
    """Read one `LABEL | sources | reason` line back.

    Lenient on purpose. Small models add preamble, drop the pipes, or answer in
    a sentence. The label word is looked for anywhere in the reply; anything
    without one comes back unclassified rather than guessed at.
    """
    stripped = text.strip()
    if not stripped:
        return ClaimLabel.UNCLASSIFIED, [], "judge returned nothing"

    label = ClaimLabel.UNCLASSIFIED
    upper = stripped.upper()
    best = len(upper) + 1
    for word, mapped in _VERDICT_WORDS.items():
        found = upper.find(word)
        if found != -1 and found < best:
            best, label = found, mapped

    parts = [p.strip() for p in stripped.split("|")]
    sources = _parse_sources(parts[1]) if len(parts) > 1 else []
    reason = parts[2] if len(parts) > 2 else stripped
    return label, sources, reason


def _parse_sources(text: str) -> list[str]:
    return [n for n in re.findall(r"\d+", text)]


class Claim(BaseModel):
    """One atomic claim from an answer, with its verdict."""

    model_config = ConfigDict(extra="forbid")

    text: str
    label: ClaimLabel
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    raw_verdict: str = ""

    @property
    def is_leak(self) -> bool:
        return self.label is ClaimLabel.LEAK


class ClaimReport(BaseModel):
    """Every claim in one answer, labelled."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    answer: str
    judge_model: str
    claims: list[Claim] = Field(default_factory=list)
    evidence_count: int = 0

    def _of(self, label: ClaimLabel) -> list[Claim]:
        return [c for c in self.claims if c.label is label]

    @property
    def grounded(self) -> list[Claim]:
        return self._of(ClaimLabel.GROUNDED)

    @property
    def benign(self) -> list[Claim]:
        return self._of(ClaimLabel.BENIGN)

    @property
    def leaks(self) -> list[Claim]:
        return self._of(ClaimLabel.LEAK)

    @property
    def unclassified(self) -> list[Claim]:
        return self._of(ClaimLabel.UNCLASSIFIED)

    @property
    def factual_claims(self) -> list[Claim]:
        """Claims that assert something checkable, so grounded plus leaks."""
        return self.grounded + self.leaks

    @property
    def groundedness(self) -> float:
        """Share of factual claims traceable to the evidence.

        Benign claims are excluded: an answer that is mostly hedging isn't
        well grounded, it just isn't asserting much.
        """
        factual = self.factual_claims
        return len(self.grounded) / len(factual) if factual else 1.0

    @property
    def leak_count(self) -> int:
        return len(self.leaks)

    @property
    def counts(self) -> dict[str, int]:
        tally = {label.value: 0 for label in ClaimLabel}
        for claim in self.claims:
            tally[claim.label.value] += 1
        return tally

    def summary(self) -> str:
        return (
            f"{len(self.claims)} claim(s) from {self.evidence_count} evidence record(s): "
            f"{len(self.grounded)} grounded, {len(self.benign)} benign, "
            f"{self.leak_count} suspected leak(s), groundedness {self.groundedness:.0%}"
        )

    def explain(self) -> str:
        lines = [self.summary()]
        if self.leaks:
            lines += ["", "Specific facts not present in the evidence the agent received:"]
            lines += [f"  {c.text}\n    {c.reason}" for c in self.leaks]
        if self.unclassified:
            lines += ["", f"{len(self.unclassified)} claim(s) the judge could not label."]
        return "\n".join(lines)


class ClaimClassifier:
    """Splits an answer into claims and labels each against the evidence.

    Args:
        client: Ollama client, or anything with a compatible `chat`.
        model: Judge model. Discovered at runtime if unset.
        max_claims: Cap on claims per answer, to bound cost.
        temperature: Zero, so a classification run is reproducible.
    """

    DECOMPOSE_PROMPT = (
        "Break the following answer into its atomic claims, one per line.\n\n"
        "Rules:\n"
        "- One self-contained statement per line. No numbering, no bullets, no commentary.\n"
        "- Split compound sentences into separate claims.\n"
        "- Keep hedges and statements of uncertainty as their own claims.\n"
        "- Use the answer's own wording. Do not add anything.\n\n"
        "Answer:\n{answer}"
    )

    # An ordered procedure, not three parallel options. Small models given a
    # menu reach for UNSUPPORTED on anything that isn't a verbatim restatement,
    # so BENIGN gets a gate of its own at the top and UNSUPPORTED is what's left
    # over rather than a choice competing with the others.
    CLASSIFY_PROMPT = (
        "You are checking one claim against a set of documents.\n\n"
        "Documents:\n{evidence}\n\n"
        "Claim: {claim}\n\n"
        "Work through these in order and stop at the first that fits.\n\n"
        "1. Is the claim a prediction, an opinion, a hedge, a statement about what is "
        "unknown or not stated, or general background knowledge? Then the label is "
        "BENIGN. Words like probably, likely, may, might, expected, unclear, does not "
        "say, or no date given are signs of this. This rule wins even when the claim "
        "is about a price, a date or a number: a guess about a number is still a "
        "guess, not an assertion of fact.\n"
        "2. Do the documents state or support the claim? The documents do not have to "
        "use the same words, and reporting what someone estimated or claimed still "
        "counts. Then the label is GROUNDED.\n"
        "3. Otherwise the claim is a specific fact, a name, number, date, price or "
        "event, that appears in no document. Then the label is UNSUPPORTED.\n\n"
        "Examples:\n"
        "Claim: The price will probably rise next year.\n"
        "BENIGN | - | a prediction, not a factual assertion\n"
        "Claim: The documents do not give a firm ship date.\n"
        "BENIGN | - | a statement about what is missing\n"
        "Claim: Analysts put the price in the $10 to $12 range. "
        "(document 1 says analysts see $10 to $12)\n"
        "GROUNDED | 1 | document 1 reports that range\n"
        "Claim: It shipped on 3 March for $500. (no document mentions either)\n"
        "UNSUPPORTED | - | that date and price appear in no document\n\n"
        "Now reply for the claim above with exactly one line, nothing else:\n"
        "LABEL | document numbers or - | short reason"
    )

    def __init__(
        self,
        client: OllamaClient | None = None,
        model: str | None = None,
        *,
        max_claims: int = DEFAULT_MAX_CLAIMS,
        temperature: float = 0.0,
    ) -> None:
        self.client = client or OllamaClient()
        self.model = model
        self.max_claims = max_claims
        self.temperature = temperature

    def classify(self, answer: str, evidence: list[EvidenceRecord]) -> ClaimReport:
        """Label every claim in `answer` against `evidence`."""
        judge = self.model or self.client.pick_model()
        report = ClaimReport(answer=answer, judge_model=judge, evidence_count=len(evidence))
        if not answer.strip():
            return report

        block = format_evidence(evidence) if evidence else "(no documents were provided)"
        index = {str(i): record.source_id for i, record in enumerate(evidence, 1)}

        for text in self._decompose(judge, answer):
            report.claims.append(self._classify_one(judge, text, block, index))
        return report

    def _decompose(self, judge: str, answer: str) -> list[str]:
        reply = self._ask(judge, self.DECOMPOSE_PROMPT.format(answer=answer))
        claims = parse_claims(reply, max_claims=self.max_claims)
        # A model that refuses to split gets taken at its word: the answer is
        # one claim. Better than dropping it and reporting nothing.
        return claims or ([answer.strip()] if answer.strip() else [])

    def _classify_one(
        self, judge: str, text: str, block: str, index: dict[str, str]
    ) -> Claim:
        reply = self._ask(
            judge, self.CLASSIFY_PROMPT.format(evidence=block, claim=text)
        ).strip()
        label, numbers, reason = parse_verdict(reply)
        return Claim(
            text=text,
            label=label,
            evidence_ids=[index[n] for n in numbers if n in index],
            reason=reason,
            raw_verdict=reply,
        )

    def _ask(self, model: str, prompt: str) -> str:
        return self.client.chat(
            model, [{"role": "user", "content": prompt}], temperature=self.temperature
        ).content


def classify_run(
    run: AgentRun,
    *,
    client: OllamaClient | None = None,
    judge_model: str | None = None,
    max_claims: int = DEFAULT_MAX_CLAIMS,
) -> ClaimReport:
    """Classify an agent run's answer against the evidence it actually received.

    The evidence here is post-filter by construction, which is the point: any
    specific fact in the answer that isn't in it did not arrive through a tool.
    """
    classifier = ClaimClassifier(client, judge_model, max_claims=max_claims)
    return classifier.classify(run.final_answer, run.evidence)
