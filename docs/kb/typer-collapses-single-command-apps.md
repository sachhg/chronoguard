---
id: typer-collapses-single-command-apps
title: Typer needs a callback to stay a command group
type: pitfall
description: With one command and no @app.callback(), Typer drops the subcommand name.
tags: [cli, bugs]
links: [module-map]
source: src/chronoguard/cli.py
---
A Typer app with exactly one command collapses into that command, so
`chronoguard version` failed with "unexpected extra argument (version)".

The fix is an `@app.callback()` on the app, which is there now and also carries
the `--version` flag. It has to stay even if the command count changes, since
removing commands could silently re-trigger the collapse.
