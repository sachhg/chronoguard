---
id: claim-label-meanings
title: What the three claim labels mean
type: contract
description: grounded, ungrounded-but-benign, suspected-parametric-leak, plus the unclassified escape hatch.
tags: [claims, api]
links: [judge-asked-observable-question, groundedness-excludes-hedges, preamble-filters-eat-real-claims]
source: src/chronoguard/claims.py
---
- `grounded`: the provided evidence states or supports it.
- `ungrounded-but-benign`: reasoning, hedging, opinion, a statement about what is
  unknown, or general background knowledge. Not a specific factual assertion, so
  nothing to leak.
- `suspected-parametric-leak`: a specific fact (name, number, date, price,
  event) that is not in the evidence and is not general background.
- `unclassified`: the judge returned something unusable.

That fourth label is not folded into the others on purpose. A judge failure and
a clean claim must never look the same in the counts, and the report lists
unclassified claims alongside leaks in its flagged section.

Note the word "suspected". The classifier observes that a fact arrived from
somewhere other than the evidence. It cannot prove where.
