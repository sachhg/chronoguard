"""A pretend council minutes API, and the adapter that maps it onto evidence.

The point of this file is the adapter. This archive returns a shape ChronoGuard
has never seen: a status wrapper around `items`, keyed on `ref` and `published`,
with the text split across `headline` and `text`. That's what real tools are
like, and it's why the adapter layer exists rather than everything being forced
into one house format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from chronoguard import MappingAdapter

CORPUS_PATH = Path(__file__).parent / "corpus.json"

#: The moment being simulated. The council had not yet voted.
AS_OF = "2024-03-01T00:00:00Z"

#: Strings that appear only in documents published at or after AS_OF, or in
#: documents with no usable date. If one of these reaches the agent, something
#: leaked. See docs/kb/canary-strings.md.
CANARIES = ("7.50", "1 October 2024", "17.8", "Rowe")


def load_corpus() -> list[dict[str, Any]]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


class CouncilArchive:
    """Keyword search over council minutes. Deterministic, no network."""

    #: Maps this archive's output onto EvidenceRecords. Nothing else in
    #: ChronoGuard needs to know what shape this API returns.
    adapter = MappingAdapter(
        content_key=("headline", "text"),
        source_key="ref",
        published_key="published",
        results_key="items",
    )

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else load_corpus()

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Search Ashcombe council minutes for `query`."""
        tokens = {t for t in re.split(r"[^a-z0-9]+", query.lower()) if t}
        scored = []
        for row in self.rows:
            hay = f"{row['headline']} {row['text']} {row['committee']}".lower()
            score = sum(1 for t in tokens if t in hay)
            if score:
                scored.append((-score, row["ref"], row))
        scored.sort(key=lambda item: (item[0], item[1]))
        return {"status": "ok", "items": [row for _, _, row in scored[:limit]]}
