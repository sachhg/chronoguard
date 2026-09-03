---
id: circular-import-via-package-root
title: Do not import from the package root inside a module
type: pitfall
description: report.py read __version__ from chronoguard/__init__, which imports report. Use _version.py.
tags: [python, bugs, architecture]
links: [module-map]
source: src/chronoguard/_version.py
---
`report.py` did `from chronoguard import __version__`. The moment `__init__.py`
started importing `report`, `import chronoguard` died on a partially initialised
module.

The version lives in `chronoguard/_version.py` now and everything reads it from
there.

The general rule: modules inside the package import from sibling modules, never
from `chronoguard` itself. The root re-exports for users; it is not a place for
internals to fetch things from.

Worth knowing that the test suite did not catch this, because tests import
submodules directly. `python -c "import chronoguard"` did. Keep that check in
mind after touching `__init__.py`.
