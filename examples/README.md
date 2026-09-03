# Examples

Domain-specific material lives here. The library in `src/chronoguard/` stays
domain-agnostic on purpose.

- **[policy_change/](policy_change/)** reasons about a fictional council's
  congestion charge decision as of a past date. Shows how to wire up your own
  corpus and your own tool output shape, which is the part the packaged fixtures
  can't demonstrate.

Each example is fictional. That's deliberate: no model has invented material in
its weights, so anything from the future appearing in an answer came through a
tool and nowhere else. With a real-world scenario you can't tell tool leakage
from parametric leakage, which is the exact distinction ChronoGuard exists to
make.
