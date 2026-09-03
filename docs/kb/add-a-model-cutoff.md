---
id: add-a-model-cutoff
title: How to add or correct a model cutoff
type: procedure
description: Edit model_cutoffs.json; matching is by family with longest-prefix fallback.
tags: [probe, config, howto]
links: [cutoffs-are-a-prior-not-evidence]
source: src/chronoguard/data/model_cutoffs.json
---
Add an entry under `cutoffs`, keyed by model family, value an ISO date:

```json
"qwen3": "2024-12-01"
```

Matching: a model name is reduced to its family by dropping any registry prefix
and the `:tag`, so `library/llama3.2:3b-instruct-q4_0` becomes `llama3.2`. Exact
family match first, then the longest key the family starts with. That is why
`llama3` and `llama3.1` can coexist.

A model with no entry produces `CutoffRisk(level="unknown")` and a reason telling
the reader to add one. Unknown is not treated as safe anywhere.

Approximate is fine and expected. See [[cutoffs-are-a-prior-not-evidence]].
