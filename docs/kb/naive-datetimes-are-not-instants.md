---
id: naive-datetimes-are-not-instants
title: A naive datetime is not a point on the timeline
type: decision
description: Timezone-naive timestamps parse to None unless assume_tz is passed. Bare date strings too.
tags: [dates, evidence]
links: [undated-records-rejected-by-default, evidence-record-contract]
source: src/chronoguard/evidence.py
---
`parse_timestamp` returns None for anything timezone-naive unless you pass
`assume_tz`. That includes bare date strings like `2023-06-01`, which
`fromisoformat` happily turns into naive midnight.

A wall-clock time with no offset is not a point on the timeline. Quietly picking
one is how you get off-by-a-day leaks, and a day is exactly the resolution at
which as-of experiments go wrong.

The escape hatch is explicit:

    EvidenceRecord.from_source(..., published_at="2023-06-01", assume_tz=timezone.utc)
    MappingAdapter(published_key="date", assume_tz=timezone.utc)

`TemporalGuard(as_of=...)` applies the same rule to its own argument and raises
with advice to add an offset.
