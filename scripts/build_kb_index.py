"""Build docs/kb/INDEX.md and docs/kb/manifest.json from note frontmatter.

Run it after adding or editing a note:

    python scripts/build_kb_index.py

tests/test_kb.py regenerates both in memory and compares, so a stale index
fails the suite rather than rotting quietly.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

KB_DIR = Path(__file__).resolve().parents[1] / "docs" / "kb"
INDEX_PATH = KB_DIR / "INDEX.md"
MANIFEST_PATH = KB_DIR / "manifest.json"

SCALAR_KEYS = ("id", "title", "type", "description", "source")
LIST_KEYS = ("tags", "links")

TYPE_ORDER = ["concept", "decision", "contract", "procedure", "pitfall", "map"]

TYPE_BLURB = {
    "concept": "The mental model. Read these first if you are new to the repo.",
    "decision": "Choices that were made deliberately, with the reasoning. Do not reverse one without reading its note.",
    "contract": "Shapes and interfaces other code depends on.",
    "procedure": "How to carry out a specific task in this repo.",
    "pitfall": "Traps, and bugs that already happened once.",
    "map": "Orientation.",
}

PREAMBLE = """# ChronoGuard knowledge base

Written for agents working in this repo. Humans probably want [the guide](../guide.md).

Every note is one idea in one file. The filename is the note id, so a link like
`[[boundary-rule-is-exclusive]]` resolves to `boundary-rule-is-exclusive.md` with
no lookup. Frontmatter carries `id`, `title`, `type`, `description`, `tags`,
`links` and `source`, where `source` points at the code the note is about.

How to use it: read this index, pick the notes whose `description` matches what
you are doing, load those and their `links`. Do not load the whole corpus. That
is the point of splitting it up.

`manifest.json` is the same data in machine-readable form.

Regenerate this file with `python scripts/build_kb_index.py`.
"""


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Read the small YAML subset the notes use. No parser dependency needed."""
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("note has no frontmatter block")

    data: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if key in LIST_KEYS:
            inner = raw.strip("[]").strip()
            data[key] = [item.strip() for item in inner.split(",") if item.strip()]
        else:
            data[key] = raw
    return data


def load_notes(kb_dir: Path = KB_DIR) -> list[dict[str, Any]]:
    """Every note, sorted by id, frontmatter parsed."""
    notes = []
    for path in sorted(kb_dir.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        note = parse_frontmatter(path.read_text(encoding="utf-8"))
        note["path"] = path.name
        notes.append(note)
    return notes


def build_index_text(notes: list[dict[str, Any]]) -> str:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        by_type.setdefault(note["type"], []).append(note)

    lines = [PREAMBLE, f"{len(notes)} notes.\n"]
    for kind in TYPE_ORDER + sorted(set(by_type) - set(TYPE_ORDER)):
        group = by_type.get(kind)
        if not group:
            continue
        lines.append(f"## {kind}\n")
        lines.append(f"{TYPE_BLURB.get(kind, '')}\n")
        for note in sorted(group, key=lambda n: n["id"]):
            lines.append(f"- [{note['id']}]({note['path']}) {note['description']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_manifest(notes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "note_count": len(notes),
        "types": sorted({n["type"] for n in notes}),
        "notes": [
            {
                "id": n["id"],
                "path": n["path"],
                "title": n["title"],
                "type": n["type"],
                "description": n["description"],
                "tags": n["tags"],
                "links": n["links"],
                "source": n["source"],
            }
            for n in notes
        ],
    }


def main() -> int:
    notes = load_notes()
    INDEX_PATH.write_text(build_index_text(notes), encoding="utf-8")
    MANIFEST_PATH.write_text(
        json.dumps(build_manifest(notes), indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {INDEX_PATH.name} and {MANIFEST_PATH.name} for {len(notes)} notes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
