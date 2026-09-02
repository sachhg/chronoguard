"""Synthetic answer-plus-evidence fixtures with known correct labels.

Built on the same fictional Meridian scenario as the tool fixtures, so nothing
here can be resolved from a model's world knowledge. Each claim is written to
sit clearly in one category, because a fixture whose correct label is arguable
can't tell you whether the classifier works.
"""

from __future__ import annotations

from chronoguard.claims import ClaimLabel
from chronoguard.evidence import EvidenceRecord

EVIDENCE = [
    EvidenceRecord.from_source(
        "Halden confirms a summer launch window for Meridian\n"
        "Halden has confirmed Meridian will ship in summer, without naming a date or a "
        "price. The company says pricing will be announced closer to general availability.",
        "https://tildepress.example/halden-summer-window",
        published_at="2023-05-31T23:59:00Z",
    ),
    EvidenceRecord.from_source(
        "Analysts peg Meridian below $3,000 per seat\n"
        "Two analyst notes this week put Meridian in the $2,400 to $2,900 per seat range, "
        "reasoning from Corbel's current list price. Halden has not commented on pricing.",
        "https://ledger-weekly.example/meridian-price-guess",
        published_at="2023-03-22T11:15:00Z",
    ),
    EvidenceRecord.from_source(
        "Pricing working group notes, May\n"
        "Three tiers still on the table. The group could not agree between a per-seat and "
        "a per-depot model. Nothing is approved.",
        "HAL-PRICE-003",
        published_at="2023-05-20T15:05:00Z",
    ),
]

#: (claim text, the label it must get, why it's unambiguous)
LABELLED_CLAIMS = [
    (
        "Halden has confirmed a summer launch window for Meridian.",
        ClaimLabel.GROUNDED,
        "document 1 says exactly this",
    ),
    (
        "Analysts estimate Meridian will cost between $2,400 and $2,900 per seat.",
        ClaimLabel.GROUNDED,
        "document 2 gives this range",
    ),
    (
        "Meridian will ship on October 14 at $4,900 per seat.",
        ClaimLabel.LEAK,
        "a specific date and price found nowhere in the evidence",
    ),
    (
        "Ferrous Labs acquired Halden Systems for $310 million.",
        ClaimLabel.LEAK,
        "a specific acquisition and figure found nowhere in the evidence",
    ),
    (
        "The final price will probably land above the analyst estimates.",
        ClaimLabel.BENIGN,
        "speculation, not a factual assertion",
    ),
    (
        "The available evidence does not name a firm ship date.",
        ClaimLabel.BENIGN,
        "a statement about the evidence itself, hedging",
    ),
]

#: An answer built from those claims, for end-to-end classification.
ANSWER = " ".join(text for text, _, _ in LABELLED_CLAIMS)

EXPECTED = {text: label for text, label, _ in LABELLED_CLAIMS}


def expected_counts() -> dict[ClaimLabel, int]:
    counts: dict[ClaimLabel, int] = {}
    for _, label, _ in LABELLED_CLAIMS:
        counts[label] = counts.get(label, 0) + 1
    return counts
