---
id: claim-and-answer-matching
title: How answers are matched: squash, then fuzzy, then judge
type: contract
description: Matching rules used by the probe, including the short-answer token rule.
tags: [probe, scoring]
links: [add-a-probe-case, probe-cases-must-be-real]
source: src/chronoguard/probe.py
---
`score_response` tries three things in order and stops at the first hit.

1. **Exact**, on squashed text: everything but letters and digits removed, so
   `GPT-4`, `GPT 4` and `gpt4` agree, and `$3,499` matches `3499`. Variants
   shorter than three squashed characters match on token boundaries instead, so
   a one-letter answer does not match every word containing that letter.
2. **Fuzzy**, sliding a window the width of the expected answer across the
   response and taking the best ratio. Window-based so a long reply cannot
   dilute the score the way whole-string similarity would.
3. **Judge**, an optional LLM call, only when the cheap paths missed and the
   model did not refuse. Off unless `judge_model` is set.

Aliases feed all three. When adding a case, add the phrasings a model would
plausibly use.
