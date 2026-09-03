---
id: cutoffs-are-a-prior-not-evidence
title: Model cutoffs are a prior, the probe is the evidence
type: decision
description: model_cutoffs.json only decides pre-scoring risk flagging; never treat it as ground truth.
tags: [probe, config]
links: [probe-has-a-control-group, add-a-model-cutoff]
source: src/chronoguard/data/model_cutoffs.json
---
`model_cutoffs.json` holds approximate training cutoffs by model family. Its
only job is to flag a run as high risk before any scoring happens, when as_of
predates the model's cutoff.

Do not treat it as ground truth and do not add logic that trusts it more than
that. Vendors are vague, post-training on recent data blurs the boundary, and
models misreport their own cutoff in both directions. The file says all of this
in its own `note` field, deliberately, so anyone opening it sees the caveat.

The probe results are the evidence. If the two disagree, the probe wins.
