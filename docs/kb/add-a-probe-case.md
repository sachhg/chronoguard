---
id: add-a-probe-case
title: How to add a probe case
type: procedure
description: Extend probe_cases.json or point at your own file.
tags: [probe, howto]
links: [probe-cases-must-be-real, claim-and-answer-matching, cutoffs-are-a-prior-not-evidence, probe-questions-must-not-leak-answers]
source: src/chronoguard/data/probe_cases.json
---
Append to `src/chronoguard/data/probe_cases.json`, or write your own file in the
same shape and pass `--cases path.json` (CLI) or `load_probe_cases(path)`.

```json
{
  "id": "unique-slug",
  "question": "A question with one specific answer.",
  "answer": "The Answer",
  "aliases": ["Answer", "alternative phrasing"],
  "knowable_from": "2024-07-05T00:00:00Z",
  "topic": "world events"
}
```

`knowable_from` needs an explicit offset. Aliases matter: they are what stops a
correct answer phrased differently from scoring as a miss.

Before adding a case, ask whether a model that knows nothing after the cutoff
could still produce the answer from the question alone. If it could, the case
measures guessing, not leakage. See [[probe-questions-must-not-leak-answers]].

The test suite asserts ids are unique and that the packaged set spans both sides
of a mid-range as-of date, so it keeps working as both probes and controls.
