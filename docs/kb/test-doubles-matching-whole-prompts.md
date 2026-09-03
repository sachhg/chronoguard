---
id: test-doubles-matching-whole-prompts
title: Match test doubles against the claim, not the whole prompt
type: pitfall
description: Few-shot examples in a prompt will match a fake client's keyword routing.
tags: [testing, bugs]
links: [tune-a-judge-prompt, ordered-procedure-beats-a-menu]
source: tests/helpers.py
---
`ScriptedJudgeClient` originally routed verdicts by checking whether a keyword
appeared anywhere in the prompt. Once the classify prompt gained few-shot
examples, the example containing "probably" matched every claim and the whole
end-to-end test came back BENIGN.

It now extracts the `Claim: ` line and matches only against that.

Generalise the lesson: a fake client that keys off prompt content is coupled to
prompt wording. Key off the smallest unambiguous slice, and be suspicious when a
test double's behaviour changes after a prompt edit.
