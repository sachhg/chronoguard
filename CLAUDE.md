# CLAUDE.md

Conventions for working in this repo.

## Writing style

Applies to everything: docs, READMEs, code comments, docstrings, commit
messages, PR descriptions, and chat replies.

- No em dashes. Ever. Use a comma, a period, parens, or a colon instead.
- Write like a programmer talking to another programmer who already knows the
  domain. Casual, direct, contractions are fine.
- Say the thing. Cut throat-clearing like "it's worth noting that",
  "it's important to remember", "let's dive into".
- Ban the usual LLM filler: delve, leverage, seamless, robust, comprehensive,
  crucial, elevate, underscore, tapestry, "a testament to", "in the realm of",
  "navigate the complexities of".
- Skip the "it's not just X, it's Y" construction and the rule-of-three
  flourish where every item has the same shape.
- No cheerleading, no "Great question!", no summarizing what you just said.
- Concrete over abstract. "Drops 41 of 60 docs" beats "significantly reduces
  the evidence set".
- Bullets should carry information, not restate the heading.

## Commits

- Author is the repo owner and nobody else. Never add `Co-Authored-By`, never
  add "Generated with Claude Code" or any other tool attribution, anywhere.
- Conventional prefixes: `feat:`, `test:`, `fix:`, `docs:`, `chore:`.
- Small and modular. One logical change per commit. Roughly: scaffolding, then
  a commit per module as its tests go green, then docs and wiring.
- Never commit red tests. Fix the code, or fix the test if the test was wrong.
- Subject line in the imperative, under ~72 chars. Body explains why, not what.

## Testing

- `pytest` is the fast offline suite. No network, no Ollama, synthetic
  fixtures only. It has to stay fast.
- Anything touching a real model gets `@pytest.mark.integration` and must skip
  with a clear message when Ollama is unreachable, never fail.
- A phase is done when its tests pass, not when it ran once by hand.

## Layout

- `src/chronoguard/` is the library. Keep it domain-agnostic: no sport, ticker,
  league, or vertical in here.
- `examples/` is where domain-specific material lives.
- `DESIGN.md` is the why. `PLAN.md` is the phase order.
