"""Deterministic fixture tools for testing the guard end to end.

Two tools over two local corpora, both about a made-up company (Halden Systems)
launching a made-up product (Meridian) in 2023. Nothing here is real, which is
the point: no model has this in its weights, so if a post-as-of string turns up
in an agent's answer it came through a tool and nowhere else. That makes tool
leakage cleanly attributable, which you can't do with a real-world scenario.

The corpora are built around `FIXTURE_AS_OF` (2023-06-01T00:00:00Z):

* Pre-as-of documents that are true at the time and sometimes wrong in
  hindsight. Analysts guessing "below $3,000", Halden promising "summer".
* Awkward documents: one published at exactly the boundary instant, one with no
  date, one with a junk date, one with a naive timestamp. Two of those carry
  post-as-of facts on purpose, so a guard that waves undated content through
  leaks immediately.
* Post-as-of documents holding the answers: the ship date, the real price, the
  acquisition. Those strings are listed in `POST_AS_OF_CANARIES`, so a test can
  just grep for them.

The two tools return deliberately different shapes (a flat list of search hits
with `url`/`date`, versus a wrapper dict of `matches` with `doc_id`/`created_utc`)
so the adapter layer has to do real work.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from chronoguard.guard import TemporalGuard
from chronoguard.interception import AuditLog, GuardedTool, MappingAdapter

__all__ = [
    "FIXTURE_AS_OF",
    "POST_AS_OF_CANARIES",
    "FakeDocumentStore",
    "FakeWebSearch",
    "build_fixture_toolset",
    "load_corpus",
]

FIXTURE_AS_OF = "2023-06-01T00:00:00Z"
"""The cutoff the fixture corpora are built around."""

POST_AS_OF_CANARIES = (
    "$4,900",
    "October 14",
    "Ferrous Labs",
    "1,240 seats",
    "$310 million",
)
"""Strings that only appear in post-as-of documents. If one of these reaches the
agent, something leaked. Grep for them in tool output and in final answers."""


def load_corpus(name: str) -> list[dict[str, Any]]:
    """Read one of the packaged JSON corpora."""
    path = resources.files("chronoguard.fixtures.data").joinpath(name)
    return json.loads(path.read_text(encoding="utf-8"))


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9$,.]+", text.lower()) if t]


class _KeywordCorpus:
    """Dumb deterministic keyword search. Same query, same results, always."""

    def __init__(self, rows: list[dict[str, Any]], id_key: str, text_keys: tuple[str, ...]) -> None:
        self.rows = rows
        self.id_key = id_key
        self.text_keys = text_keys

    def _haystack(self, row: dict[str, Any]) -> str:
        return " ".join(str(row.get(k, "")) for k in self.text_keys).lower()

    def rank(self, query: str, limit: int | None) -> list[dict[str, Any]]:
        tokens = set(_tokenize(query))
        scored = []
        for row in self.rows:
            hay = self._haystack(row)
            score = sum(1 for t in tokens if t in hay)
            if score:
                scored.append((-score, str(row[self.id_key]), row))
        scored.sort(key=lambda item: (item[0], item[1]))
        ranked = [row for _, _, row in scored]
        return ranked if limit is None else ranked[:limit]


class FakeWebSearch:
    """A stand-in web search over a local corpus of dated news items.

    Returns a flat list of hits keyed on `url` and `date`, the way a lot of
    search APIs do.
    """

    corpus_name = "web_corpus.json"

    #: Maps this tool's output shape onto evidence records.
    adapter = MappingAdapter(
        content_key=("title", "snippet"),
        source_key="url",
        published_key="date",
    )

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else load_corpus(self.corpus_name)
        self._index = _KeywordCorpus(self.rows, "url", ("title", "snippet", "domain"))

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search the web for `query` and return up to `limit` hits."""
        return self._index.rank(query, limit)

    __call__ = search


class FakeDocumentStore:
    """A stand-in RAG retriever over an internal wiki.

    Returns a wrapper dict (`{"query": ..., "matches": [...]}`) keyed on
    `doc_id` and `created_utc`, deliberately unlike the search tool.
    """

    corpus_name = "doc_store.json"

    #: Maps this tool's output shape onto evidence records.
    adapter = MappingAdapter(
        content_key=("heading", "body"),
        source_key="doc_id",
        published_key="created_utc",
        results_key="matches",
    )

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else load_corpus(self.corpus_name)
        self._index = _KeywordCorpus(self.rows, "doc_id", ("heading", "body", "space"))

    def retrieve(self, query: str, k: int = 3) -> dict[str, Any]:
        """Retrieve up to `k` internal documents relevant to `query`."""
        return {"query": query, "matches": self._index.rank(query, k)}

    __call__ = retrieve


def build_fixture_toolset(
    guard: TemporalGuard,
    audit: AuditLog | None = None,
) -> dict[str, GuardedTool]:
    """Both fixture tools, guarded and sharing one audit log.

    This is what an agent gets handed in the phase 3 runner.
    """
    audit = audit if audit is not None else AuditLog()
    web = FakeWebSearch()
    store = FakeDocumentStore()
    return {
        "web_search": GuardedTool(
            web.search, guard, web.adapter, name="web_search", audit=audit
        ),
        "document_store": GuardedTool(
            store.retrieve, guard, store.adapter, name="document_store", audit=audit
        ),
    }
