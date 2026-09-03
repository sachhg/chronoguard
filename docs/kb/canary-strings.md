---
id: canary-strings
title: Canary strings are how leakage is verified
type: concept
description: POST_AS_OF_CANARIES lists strings that only exist in post-as-of fixtures; tests grep for them.
tags: [testing, fixtures]
links: [fictional-fixture-scenario, test-that-the-raw-tool-leaks-first, add-a-fixture-document]
source: src/chronoguard/fixtures/tools.py
---
`POST_AS_OF_CANARIES` holds strings that appear only in fixture documents
published at or after `FIXTURE_AS_OF`: `$4,900`, `October 14`, `Ferrous Labs`,
`1,240 seats`, `$310 million`.

Any test that wants to prove something did not leak greps for these in the thing
under test: tool output, an agent's answer, the evidence it cited, a rendered
report, a JSON summary.

Two invariants hold the fixtures together and are themselves tested:

- No document the guard *allows* contains a canary.
- Some rejected documents deliberately do contain them, including undated ones,
  so a guard that waves undated content through fails immediately.

If you add fixture documents, keep both invariants true.
