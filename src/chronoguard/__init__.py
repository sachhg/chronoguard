"""ChronoGuard: a point-in-time leakage guard for LLM agents.

ChronoGuard lets you run an arbitrary LLM agent *as if* it were operating at a
past date, and measures how well that blinding actually holds.

Two independent leakage channels are handled separately (see DESIGN.md):

* **Tool leakage** -- the agent retrieves evidence published after the as-of
  date. Fixed by filtering: :mod:`chronoguard.evidence`, :mod:`chronoguard.guard`.
* **Parametric leakage** -- the model's own weights already encode post-as-of
  facts. Not fixable by filtering; only measurable. Handled by the probing and
  claim-classification layers.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
