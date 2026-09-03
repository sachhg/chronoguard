---
id: judge-asked-observable-question
title: The judge is asked the observable question
type: decision
description: The LLM judge decides 'is this fact in the documents', not 'is this parametric leakage'.
tags: [claims, prompting]
links: [claim-label-meanings, tune-a-judge-prompt]
source: src/chronoguard/claims.py
---
The classify prompt never mentions leakage, training data or model internals. It
asks whether a claim is supported by the supplied documents, and answers
GROUNDED, BENIGN or UNSUPPORTED.

`chronoguard` maps UNSUPPORTED to `suspected-parametric-leak`. The loaded name is
applied by the library, not asked of the judge.

Two reasons. Asking a model to speculate about another model's internals is
asking for noise. And keeping the judge's job narrow and observable is what makes
a 4B local model usable for it at all.

If you extend the judge, keep its questions observable.
