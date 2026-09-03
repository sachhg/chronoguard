---
id: ordered-procedure-beats-a-menu
title: Give small models an ordered procedure, not a menu
type: pitfall
description: Three parallel label options made gemma3:4b pick UNSUPPORTED for everything; ordering fixed it.
tags: [prompting, claims]
links: [tune-a-judge-prompt, judge-asked-observable-question]
source: src/chronoguard/claims.py
---
The classify prompt first offered three labels as parallel options. gemma3:4b
scored 3/6 on the known-label fixtures: it reached for UNSUPPORTED on anything
that was not a verbatim restatement and never picked BENIGN at all.

Two changes, measured one at a time:

1. Turn it into an ordered procedure. Gate BENIGN first ("is this a specific
   factual assertion at all?"), then GROUNDED, and leave UNSUPPORTED as the
   fallthrough rather than a competing choice. That got 5/6.
2. Say the hedge rule wins even when the claim is about a number: "a guess about
   a number is still a guess". That got 6/6.

Applies beyond this prompt. When a small model overuses one option, the fix is
usually decision order and an explicit gate, not more description of each option.
