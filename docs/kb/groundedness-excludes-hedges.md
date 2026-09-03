---
id: groundedness-excludes-hedges
title: Groundedness leaves benign claims out of the denominator
type: contract
description: Only grounded plus leaked claims count, so a hedge-heavy answer cannot look well grounded.
tags: [claims, scoring]
links: [claim-label-meanings]
source: src/chronoguard/claims.py
---
`ClaimReport.groundedness` is `len(grounded) / len(grounded + leaks)`.

Benign claims are excluded. If they counted, an answer that was mostly hedging
would score near-perfect groundedness while asserting almost nothing. That is
not a well-grounded answer, it is an evasive one, and the metric should not
reward it.

An answer with no factual claims at all returns 1.0, since there is nothing
ungrounded in it. Read that alongside the claim count, not on its own.
