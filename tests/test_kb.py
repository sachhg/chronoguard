"""Validates the agent knowledge base in docs/kb.

A knowledge base that nothing checks rots: ids drift from filenames, links point
at notes that were renamed, the index goes stale, notes get written that nothing
links to. These tests are what stop that.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
KB = REPO / "docs" / "kb"
sys.path.insert(0, str(REPO / "scripts"))

from build_kb_index import (  # noqa: E402
    LIST_KEYS,
    SCALAR_KEYS,
    TYPE_ORDER,
    build_index_text,
    build_manifest,
    load_notes,
)

NOTES = load_notes(KB)
IDS = {n["id"] for n in NOTES}
WIKILINK = re.compile(r"\[\[([a-z0-9-]+)\]\]")

# By codepoint, so this file does not trip its own check.
EM_DASH = chr(0x2014)


def body_of(note: dict) -> str:
    text = (KB / note["path"]).read_text(encoding="utf-8")
    return text.split("---", 2)[2]


class TestStructure:
    def test_the_kb_is_not_empty(self) -> None:
        assert len(NOTES) >= 20

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_every_note_has_the_required_frontmatter(self, note: dict) -> None:
        for key in SCALAR_KEYS + LIST_KEYS:
            assert key in note, f"{note['path']} is missing {key}"
            assert note[key] != "", f"{note['path']} has an empty {key}"

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_the_filename_is_the_id(self, note: dict) -> None:
        # This is what makes [[links]] resolvable without a lookup table.
        assert note["path"] == f"{note['id']}.md"

    def test_ids_are_unique(self) -> None:
        ids = [n["id"] for n in NOTES]
        assert len(ids) == len(set(ids))

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_the_type_is_one_we_recognise(self, note: dict) -> None:
        assert note["type"] in TYPE_ORDER

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_the_description_is_one_useful_line(self, note: dict) -> None:
        # The index is read to decide what to load, so descriptions do real work.
        assert "\n" not in note["description"]
        assert 20 <= len(note["description"]) <= 160, note["id"]

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_notes_stay_small_enough_to_load_several(self, note: dict) -> None:
        lines = body_of(note).strip().splitlines()
        assert len(lines) <= 45, f"{note['id']} is {len(lines)} lines, split it"

    def test_every_type_is_represented(self) -> None:
        assert {n["type"] for n in NOTES} == set(TYPE_ORDER)


class TestLinks:
    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_frontmatter_links_resolve(self, note: dict) -> None:
        for target in note["links"]:
            assert target in IDS, f"{note['id']} links to missing note {target}"

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_a_note_does_not_link_to_itself(self, note: dict) -> None:
        assert note["id"] not in note["links"]

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_wikilinks_in_bodies_resolve(self, note: dict) -> None:
        for target in WIKILINK.findall(body_of(note)):
            assert target in IDS, f"{note['id']} body links to missing note {target}"

    def test_no_note_is_orphaned(self) -> None:
        linked = {t for n in NOTES for t in n["links"]}
        linked |= {t for n in NOTES for t in WIKILINK.findall(body_of(n))}
        orphans = sorted(IDS - linked)
        assert not orphans, f"nothing links to: {orphans}"

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_every_note_links_somewhere(self, note: dict) -> None:
        assert note["links"], f"{note['id']} is a dead end"


class TestSources:
    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_the_source_path_exists(self, note: dict) -> None:
        # A note pointing at a file that was moved or deleted is worse than no
        # note, because it sends a reader somewhere wrong with confidence.
        assert (REPO / note["source"]).exists(), f"{note['id']} points at missing {note['source']}"


class TestIndex:
    def test_the_index_is_up_to_date(self) -> None:
        expected = build_index_text(NOTES)
        actual = (KB / "INDEX.md").read_text(encoding="utf-8")
        assert actual == expected, "run `python scripts/build_kb_index.py`"

    def test_the_manifest_is_up_to_date(self) -> None:
        expected = build_manifest(NOTES)
        actual = json.loads((KB / "manifest.json").read_text(encoding="utf-8"))
        assert actual == expected, "run `python scripts/build_kb_index.py`"

    def test_the_index_lists_every_note(self) -> None:
        text = (KB / "INDEX.md").read_text(encoding="utf-8")
        for note in NOTES:
            assert f"]({note['path']})" in text

    def test_the_manifest_carries_what_a_loader_needs(self) -> None:
        manifest = json.loads((KB / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["note_count"] == len(NOTES)
        entry = manifest["notes"][0]
        assert set(entry) == {"id", "path", "title", "type", "description", "tags", "links", "source"}


class TestHouseStyle:
    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_no_em_dashes(self, note: dict) -> None:
        # CLAUDE.md bans them everywhere, and the kb is not an exception.
        assert EM_DASH not in (KB / note["path"]).read_text(encoding="utf-8")

    @pytest.mark.parametrize("note", NOTES, ids=lambda n: n["id"])
    def test_no_llm_filler_vocabulary(self, note: dict) -> None:
        text = (KB / note["path"]).read_text(encoding="utf-8").lower()
        banned = ["delve", "seamless", "leverage", "tapestry", "a testament to", "in the realm of"]
        assert [w for w in banned if w in text] == []
