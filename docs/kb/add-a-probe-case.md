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

Check your work with `chronoguard cases --as-of <date>`, which needs no model.
It reports how many cases are future and how many are control at that date, and
flags duplicate ids, empty answers, and questions that spell their own answer
out verbatim. It cannot tell you whether a fact or its date is *correct*, and a
wrong `knowable_from` turns a control into a false leak that silently corrupts
the score, so verify anything you did not add yourself.

The test suite asserts ids are unique and that the packaged set spans both sides
of a mid-range as-of date, so it keeps working as both probes and controls.

## The set runs out

Cases are real facts, so the packaged set stops somewhere and a probe past that
point has nothing to ask. It then scores 0/0, which reads exactly like a
well-blinded model, which is why `chronoguard cases` calls that an error rather
than letting a reassuring number through. Keeping it current is a maintenance
job nobody else can do for you: whoever adds cases has to know the facts, and
that includes any model helping out, whose own cutoff is the limit on what it
can add.
