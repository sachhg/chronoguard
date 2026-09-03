# Reading a ChronoGuard report

A report has three sections and one verdict. The verdict is the part people get
wrong, so start there.

## The verdict is not "did the filter work"

The filter almost always works. It's a timestamp comparison. If a report says
eight records were withheld, eight records were withheld.

What the verdict answers is different: **can I trust this run as a
reconstruction of what was knowable at the as-of date?** That question depends
on things the filter can't see, so a run where the filter did its job perfectly
can still be worthless.

| Verdict | What it means | What to do |
| --- | --- | --- |
| `low` | Filter held, the model showed no knowledge of the period, every factual claim traced back to evidence. | Trust the run. |
| `elevated` | Something leaked, or the model's training runs past your as-of date. | Usable with caveats. Report the number alongside your result. |
| `high` | The answer asserted facts it was never given, or the model reproduces most post-as-of facts unaided. | Do not treat the run as point-in-time. Change models. |
| `unknown` | Not enough signal. A measurement was skipped, or the probe's controls failed. | Fix the gap and rerun. Do not read this as clean. |

The worst signal wins, and every verdict carries its reasons.

## The case that catches people out

```
RISK: ELEVATED
  - the model reproduced 2 post-as-of fact(s) with no evidence in context
  - the model's training data runs past the simulated date (2024-08-01)

TOOL LEAKAGE (contained by filtering)
  kept 7, filtered 8

CLAIMS IN THE ANSWER
  6 claim(s): 5 grounded, 1 benign, 0 suspected leak(s), groundedness 100%
```

Every containment number is good. Zero leaked claims, perfect groundedness. And
the run still isn't `low`.

The reason is the middle section. That model, asked directly with no documents
at all, hands over two facts from after the as-of date, and its training runs a
year past the moment being simulated. This particular answer happened not to use
any of that. The next one might. You got a clean answer, not a blinded model,
and those are different things.

A tool that printed only the first and third sections would call this run fine.

## Section by section

### Tool leakage

`kept N, filtered M` with a verdict breakdown. This is containment and it is
reliable.

Worth reading the verdict counts rather than just the total. A pile of `undated`
or `unparseable` means your corpus has a timestamp quality problem, not a
leakage problem, and you are throwing away evidence you could be using. Fix the
dates rather than reaching for `allow_undated`.

`filtered 0` is worth a second look. Either your corpus genuinely predates the
cutoff, or nothing is dated and you have `allow_undated` on.

### Parametric leakage

`leakage X/Y, control A/B, risk L`.

Read both numbers. Leakage on its own is ambiguous:

- Low leakage, high control: the model can answer questions and doesn't know
  your period. This is the good case.
- Low leakage, low control: the model can't answer anything. Its clean score
  means nothing. Reported as `inconclusive`.
- High leakage: the model knows the period. No amount of filtering changes that.

`cutoff risk` is a separate, weaker signal from the cutoff table. It's a prior,
not evidence.

The two disagreeing is common on small models, and the report keeps both rather
than collapsing them. Measured at `as_of=2023-06-01`:

| Model | Leakage | Controls | Cutoff risk |
| --- | --- | --- | --- |
| `gemma3:4b` | 2/8 (25%) | 6/6 | high |
| `qwen3:4b` | 1/8 (12%) | 6/6 | high |

qwen3 looks cleaner, but its wrong answers give it away: asked about the 2024
physics Nobel it names the 2023 laureates, and asked about the 2024 Turing Award
it names the 2018 winners. It has clearly seen 2024. It just recalls specific
award winners badly at 4B.

So a low leakage score is evidence about *those facts*, not proof of an early
cutoff. The control group catches a model that can't answer anything; it doesn't
catch one that handles easy old facts and fluffs recent ones, because controls
skew older by construction. Bigger models make the probe more informative,
because a miss is more likely to be real ignorance than a recall failure.

### Claims

`N claims: G grounded, B benign, L suspected leaks, groundedness P%`.

Flagged claims are the ones to read. Each says what it asserted and why the
judge couldn't find it in the evidence.

Two things to keep in mind. "Suspected" is doing real work: the classifier
observes that a fact arrived from somewhere other than the evidence, it can't
prove where from. And `groundedness` excludes benign claims, so an answer that
hedges its way to 100% while asserting almost nothing is possible. Read it next
to the claim count.

Judge quality varies by model. If claim labels look wrong, check the judge
against the fixtures in `tests/claim_fixtures.py` before trusting the numbers.

## What to do about a bad verdict

**High, from flagged claims.** The agent asserted something it wasn't given.
Read the flagged claims; if they're real facts about your domain, that's
parametric leakage in the output and the run is not usable as-is.

**High or elevated, from the probe.** The model knows your period. The only real
fix is a different model, one whose training predates your as-of date. Check
`chronoguard models` and the cutoff table. Failing that, report the leakage
score alongside your result so readers can discount it.

**Unknown, from a skipped stage.** Rerun without `--skip-probe` or
`--skip-claims`.

**Unknown, from failed controls.** The model is too weak to interpret. Use a
bigger one, or check whether your probe cases are unreasonably hard.

**A run that times out.** `OllamaTimeout` means the server is up and the model
is slow, not that anything is broken. Reasoning models spend a long time
thinking: a single qwen3:4b claim classification on a long evidence block can
exceed three minutes. Cap the work with `--max-future`, `--max-control` and
`--max-claims`, or use a non-reasoning model as the judge.

## Diffing runs

`--json-out` writes a stable shape, so the useful move is committing summaries
and diffing them across model or corpus changes. `tool_leakage.records_filtered`,
`parametric_leakage.leakage_score` and `claims.flagged` are the three numbers
worth tracking. A skipped stage is `null` rather than a missing key, so consumers
don't have to guard for absence.
