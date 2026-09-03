---
id: fictional-fixture-scenario
title: The fixture scenario is fictional on purpose
type: decision
description: Halden Systems and Meridian are invented so any leak is attributable to a tool, not to training.
tags: [fixtures, testing]
links: [canary-strings, test-that-the-raw-tool-leaks-first, add-a-fixture-document]
source: src/chronoguard/fixtures/data/
---
The tool fixtures are about a made-up company launching a made-up product in
2023. Nothing in them is real.

That is what makes the integration tests mean anything. No model has this in its
weights, so a post-as-of string appearing in an agent's answer came through a
tool and nowhere else. With a real-world scenario you could never tell tool
leakage from parametric leakage, which is the exact distinction the project is
about.

The corpus is also built so a grounded agent and a leaking agent give visibly
different answers: pre-as-of documents carry the plausible wrong answer
(analysts guessing "below $3,000", Halden promising "summer") while the real
ship date and price exist only in post-as-of documents.

Probe cases are the opposite: those have to be real, because the point is
testing real training data. See [[probe-cases-must-be-real]].
