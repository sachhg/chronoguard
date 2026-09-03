---
id: falsy-zero-limits
title: Do not test caps with a bare truthiness check
type: pitfall
description: `if max_control` treated an explicit 0 as 'no limit'. Use `is not None`.
tags: [python, bugs]
links: [capped-probe-runs-take-nearest-cases]
source: src/chronoguard/probe.py
---
This shipped once and got caught by a test:

```python
control[:max_control] if max_control else control   # wrong
control if max_control is None else control[:max_control]   # right
```

`--max-control 0` means "ask no control questions". The truthy check read it as
"no limit" and asked all of them.

Any optional integer that can legitimately be zero needs `is not None`. In this
repo that is every `max_*` cap.
