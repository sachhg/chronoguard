---
id: cli-output-assertions-must-strip-ansi
title: Strip ANSI before asserting on CLI output
type: pitfall
description: Rich formats help text differently under CI, so substring assertions passed locally and failed on GitHub Actions.
tags: [testing, cli, ci]
links: [run-the-test-suites, typer-collapses-single-command-apps]
source: tests/test_cli.py
---

`assert "Usage: chronoguard" in result.output` passed locally for weeks and
failed on the first CI run. Rich renders the help panel with bold and padding
escape codes, and under CI it inserts them in different places, so the substring
was split across escape sequences.

Two defences, both in place:

- The shared `CliRunner` is built with `COLUMNS=200`, `NO_COLOR=1` and
  `TERM=dumb`, so output is wide and plain.
- Assertions go through `plain(result)`, which strips escape codes anyway.

Use `plain(result)` for any new substring assertion on CLI output. The env
settings alone are not enough, since a library can still emit colour when it
feels like it.

Reproduce a CI-like environment locally with:

    env -i PATH="$PATH" HOME="$HOME" TERM=xterm-256color FORCE_COLOR=1 pytest tests/test_cli.py
