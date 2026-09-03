"""Keeps the prose honest.

Documentation rots in two boring ways: links point at files that moved, and
house style slips. Both are cheap to check and annoying to find by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# By codepoint, so this file does not trip its own check.
EM_DASH = chr(0x2014)

MARKDOWN = sorted(
    p
    for p in REPO.rglob("*.md")
    if not any(part in {".venv", ".git", "node_modules"} for part in p.parts)
)

# [text](target), ignoring images and bare autolinks.
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")

BANNED_WORDS = [
    "delve",
    "seamless",
    "leverage",
    "tapestry",
    "a testament to",
    "in the realm of",
    "navigate the complexities",
]


def ids(path: Path) -> str:
    return str(path.relative_to(REPO))


class TestLinks:
    @pytest.mark.parametrize("path", MARKDOWN, ids=ids)
    def test_relative_links_resolve(self, path: Path) -> None:
        broken = []
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(target)
        assert not broken, f"{ids(path)} links to missing: {broken}"

    def test_the_readme_points_at_every_top_level_doc(self) -> None:
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for expected in (
            "docs/guide.md",
            "docs/configuration.md",
            "docs/interpreting-reports.md",
            "docs/kb/INDEX.md",
            "DESIGN.md",
            "PLAN.md",
            "LICENSE",
            "examples/",
        ):
            assert expected in readme, f"README does not mention {expected}"


class TestHouseStyle:
    @pytest.mark.parametrize("path", MARKDOWN, ids=ids)
    def test_no_em_dashes(self, path: Path) -> None:
        assert EM_DASH not in path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("path", MARKDOWN, ids=ids)
    def test_no_llm_filler_vocabulary(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8").lower()
        # CLAUDE.md lists these as banned, so it is allowed to name them.
        if path.name == "CLAUDE.md":
            return
        assert [w for w in BANNED_WORDS if w in text] == []


class TestSourceStyle:
    @pytest.mark.parametrize(
        "path",
        sorted((REPO / "src").rglob("*.py")) + sorted((REPO / "tests").rglob("*.py")),
        ids=lambda p: str(p.relative_to(REPO)),
    )
    def test_no_em_dashes_in_code_or_comments(self, path: Path) -> None:
        assert EM_DASH not in path.read_text(encoding="utf-8")
