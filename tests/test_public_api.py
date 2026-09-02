"""The public surface stays importable from the package root."""

from __future__ import annotations

import chronoguard


def test_everything_in_all_is_importable() -> None:
    for name in chronoguard.__all__:
        assert hasattr(chronoguard, name), f"{name} is in __all__ but missing"


def test_quickstart_from_the_docstring_works() -> None:
    guard = chronoguard.TemporalGuard("2023-06-01T00:00:00Z")
    result = guard.filter(
        [
            chronoguard.EvidenceRecord.from_source(
                "before", "doc-1", published_at="2023-05-04T09:00:00Z"
            ),
            chronoguard.EvidenceRecord.from_source(
                "after", "doc-2", published_at="2023-08-11T09:00:00Z"
            ),
        ]
    )
    assert [r.source_id for r in result.kept] == ["doc-1"]
    assert result.filtered_count == 1
