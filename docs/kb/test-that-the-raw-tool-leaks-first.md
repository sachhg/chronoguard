---
id: test-that-the-raw-tool-leaks-first
title: Assert the unguarded path leaks before asserting the guarded one doesn't
type: pitfall
description: A leak test passes trivially if the tool never returned the future in the first place.
tags: [testing, fixtures]
links: [canary-strings, fictional-fixture-scenario]
source: tests/test_fixture_tools.py
---
`TestRawToolsLeak` asserts the *unguarded* fixture tools do return post-as-of
content. Only then do the guarded tests assert they don't.

Without that, a corpus change that stopped surfacing post-as-of documents would
make every leak test pass while testing nothing. The same reasoning is why the
live agent test asserts the model actually called a tool and the guard actually
dropped something, and why the live probe test asserts the control score is
above zero.

Any new "X does not leak" test needs a partner asserting X could have.
