---
id: numbers-are-matched-exactly
title: Numbers are matched exactly, never fuzzily
type: contract
description: fuzzy_match requires every digit run in a variant to appear, because one digit apart is a different fact.
tags: [probe, scoring]
links: [claim-and-answer-matching, probe-questions-must-not-leak-answers]
source: src/chronoguard/probe.py
---

`fuzzy_match` requires every digit run in a variant to be present in the window
before it will even compute a similarity ratio.

Without that rule, qwen3:4b answering the OpenAI ouster date as "November 13,
2023" scored as a leak against an expected "17 November 2023". The alias
"November 17" matched the window "november 13" at 0.91, because one digit apart
is nearly identical on characters and a completely different fact. The same trap
catches prices, quantities and version numbers: GPT-3.5 matched GPT-4 the same
way.

Names keep their typo tolerance, since "Geoffrey Hintno" carries no digits.

Fixing this surfaced a second bug. `normalize` stripped every separator between
digits, including spaces, so "17 2023" collapsed to "172023" and a date's two
numbers looked like one. Only thousands separator commas come out now.

That same line carried a stray U+202F narrow no-break space inside its character
class, which is why it resisted several literal edits. If a `str.replace` on
source that looks correct keeps failing, print the line with `repr` before
assuming the search string is wrong.
