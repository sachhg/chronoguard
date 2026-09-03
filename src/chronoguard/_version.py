"""Single source of the version.

Lives on its own so modules can read it without importing the package root,
which imports them back.
"""

__version__ = "0.1.0"
