---
id: tune-a-judge-prompt
title: How to tune a judge prompt against fixtures
type: procedure
description: Measure against known labels before and after; do not guess at prompt changes.
tags: [claims, prompting, howto]
links: [judge-asked-observable-question, ordered-procedure-beats-a-menu, measure-prompt-changes-on-a-set]
source: tests/claim_fixtures.py
---
`tests/claim_fixtures.py` holds answer-plus-evidence fixtures whose correct
labels are fixed in advance. Use them as a scoreboard, not as decoration.

```python
import sys; sys.path.insert(0, "tests")
from chronoguard.agent import format_evidence
from chronoguard.claims import ClaimClassifier
from chronoguard.ollama import OllamaClient
from claim_fixtures import EVIDENCE, LABELLED_CLAIMS

clf = ClaimClassifier(OllamaClient(), "gemma3:4b")
block = format_evidence(EVIDENCE)
index = {str(i): r.source_id for i, r in enumerate(EVIDENCE, 1)}
for text, expected, _ in LABELLED_CLAIMS:
    got = clf._classify_one("gemma3:4b", text, block, index)
    print(got.label is expected, got.label.value, text[:50])
```

Record the score before you change anything, change one thing, score again. The
current prompt went 3/6, then 5/6, then 6/6 on gemma3:4b that way. See
[[ordered-procedure-beats-a-menu]] for what actually moved it, and
[[measure-prompt-changes-on-a-set]] for what happens when you skip the scoring.
