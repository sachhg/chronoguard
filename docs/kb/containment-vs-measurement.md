---
id: containment-vs-measurement
title: Which module does containment, which does measurement
type: concept
description: Layer 1 contains tool leakage; layer 2 measures parametric leakage. Map of what belongs where.
tags: [core, architecture]
links: [two-leakage-channels, module-map]
source: src/chronoguard/
---
Layer 1, containment. Deterministic, offline, fully unit-testable.

- `evidence.py` normalises anything into one record type
- `guard.py` decides what predates as_of
- `interception.py` puts the guard in front of any tool

Layer 2, measurement. Statistical, needs a model, so scoring logic is unit
tested and the model-in-the-loop parts sit behind `@pytest.mark.integration`.

- `probe.py` measures what the model knew before the run started
- `claims.py` measures what the answer asserted that the evidence never supplied
- `report.py` combines all three into one verdict

If you find yourself trying to make layer 1 smarter about what the model knows,
stop. That is layer 2's job and it needs a model to do it.
