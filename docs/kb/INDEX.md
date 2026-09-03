# ChronoGuard knowledge base

Written for agents working in this repo. Humans probably want [the guide](../guide.md).

Every note is one idea in one file. The filename is the note id, so a link like
`[[boundary-rule-is-exclusive]]` resolves to `boundary-rule-is-exclusive.md` with
no lookup. Frontmatter carries `id`, `title`, `type`, `description`, `tags`,
`links` and `source`, where `source` points at the code the note is about.

How to use it: read this index, pick the notes whose `description` matches what
you are doing, load those and their `links`. Do not load the whole corpus. That
is the point of splitting it up.

`manifest.json` is the same data in machine-readable form.

Regenerate this file with `python scripts/build_kb_index.py`.

42 notes.

## concept

The mental model. Read these first if you are new to the repo.

- [canary-strings](canary-strings.md) POST_AS_OF_CANARIES lists strings that only exist in post-as-of fixtures; tests grep for them.
- [containment-vs-measurement](containment-vs-measurement.md) Layer 1 contains tool leakage; layer 2 measures parametric leakage. Map of what belongs where.
- [two-leakage-channels](two-leakage-channels.md) The central model: tool leakage is containable, parametric leakage is only measurable.

## decision

Choices that were made deliberately, with the reasoning. Do not reverse one without reading its note.

- [agent-never-told-what-was-filtered](agent-never-told-what-was-filtered.md) Filter counts go to the audit log, never into the model's context.
- [boundary-rule-is-exclusive](boundary-rule-is-exclusive.md) published_at == as_of is rejected. Why, and how to get inclusive behaviour instead.
- [capped-probe-runs-take-nearest-cases](capped-probe-runs-take-nearest-cases.md) max_future_cases keeps cases closest to the as-of date, because those are where leakage shows.
- [cutoffs-are-a-prior-not-evidence](cutoffs-are-a-prior-not-evidence.md) model_cutoffs.json only decides pre-scoring risk flagging; never treat it as ground truth.
- [fictional-fixture-scenario](fictional-fixture-scenario.md) Halden Systems and Meridian are invented so any leak is attributable to a tool, not to training.
- [judge-asked-observable-question](judge-asked-observable-question.md) The LLM judge decides 'is this fact in the documents', not 'is this parametric leakage'.
- [naive-datetimes-are-not-instants](naive-datetimes-are-not-instants.md) Timezone-naive timestamps parse to None unless assume_tz is passed. Bare date strings too.
- [probe-cases-must-be-real](probe-cases-must-be-real.md) Unlike tool fixtures, probe cases test actual training data so they cannot be invented.
- [probe-does-not-ask-model-to-pretend](probe-does-not-ask-model-to-pretend.md) Probe questions are asked straight, because pretending measures compliance not knowledge.
- [probe-has-a-control-group](probe-has-a-control-group.md) Zero leakage from a model that can't answer anything is not evidence of blinding.
- [prompt-is-not-containment](prompt-is-not-containment.md) The as-of line in the prompt keeps the model on task; the guard is what actually blocks the future.
- [undated-records-rejected-by-default](undated-records-rejected-by-default.md) No timestamp, junk timestamp or naive timestamp means rejected unless allow_undated is set.
- [verdict-never-reports-unearned-clean](verdict-never-reports-unearned-clean.md) Two rules stop a spotless-looking run from reading as low risk.

## contract

Shapes and interfaces other code depends on.

- [adapter-interface](adapter-interface.md) The three adapter kinds and when each applies.
- [audit-log-is-the-reporting-side](audit-log-is-the-reporting-side.md) Share one log across an agent's tools; that is where filtered counts come from.
- [claim-and-answer-matching](claim-and-answer-matching.md) Matching rules used by the probe, including the short-answer token rule.
- [claim-label-meanings](claim-label-meanings.md) grounded, ungrounded-but-benign, suspected-parametric-leak, plus the unclassified escape hatch.
- [evidence-record-contract](evidence-record-contract.md) Fields, the two constructors, and when to use which.
- [groundedness-excludes-hedges](groundedness-excludes-hedges.md) Only grounded plus leaked claims count, so a hedge-heavy answer cannot look well grounded.
- [guarded-tool-contract](guarded-tool-contract.md) What wrapping changes, what it preserves, and what not to wrap.
- [probe-case-boundary-matches-the-guard](probe-case-boundary-matches-the-guard.md) knowable_from >= as_of makes a case a probe, matching published_at >= as_of being a violation.
- [scenario-summary-schema](scenario-summary-schema.md) Top-level keys of ScenarioReport.summary() and what consumers can rely on.

## procedure

How to carry out a specific task in this repo.

- [add-a-fixture-document](add-a-fixture-document.md) Two invariants the tool corpora must keep, enforced by corpus-design tests.
- [add-a-guarded-tool](add-a-guarded-tool.md) Wrap any callable returning evidence so an agent only sees pre-as-of results.
- [add-a-model-cutoff](add-a-model-cutoff.md) Edit model_cutoffs.json; matching is by family with longest-prefix fallback.
- [add-a-probe-case](add-a-probe-case.md) Extend probe_cases.json or point at your own file.
- [run-the-test-suites](run-the-test-suites.md) Fast offline suite versus the Ollama-backed integration suite.
- [tune-a-judge-prompt](tune-a-judge-prompt.md) Measure against known labels before and after; do not guess at prompt changes.

## pitfall

Traps, and bugs that already happened once.

- [circular-import-via-package-root](circular-import-via-package-root.md) report.py read __version__ from chronoguard/__init__, which imports report. Use _version.py.
- [falsy-zero-limits](falsy-zero-limits.md) `if max_control` treated an explicit 0 as 'no limit'. Use `is not None`.
- [integration-tests-skip-never-fail](integration-tests-skip-never-fail.md) Fixtures in conftest.py skip with an actionable message; keep new model-backed tests behind them.
- [measure-prompt-changes-on-a-set](measure-prompt-changes-on-a-set.md) Two prompt "fixes" driven by a single misclassified claim both made the judge worse overall.
- [ordered-procedure-beats-a-menu](ordered-procedure-beats-a-menu.md) Three parallel label options made gemma3:4b pick UNSUPPORTED for everything; ordering fixed it.
- [preamble-filters-eat-real-claims](preamble-filters-eat-real-claims.md) A preamble regex listing 'claims?|answer' silently dropped genuine claims starting with those words.
- [test-doubles-matching-whole-prompts](test-doubles-matching-whole-prompts.md) Few-shot examples in a prompt will match a fake client's keyword routing.
- [test-that-the-raw-tool-leaks-first](test-that-the-raw-tool-leaks-first.md) A leak test passes trivially if the tool never returned the future in the first place.
- [typer-collapses-single-command-apps](typer-collapses-single-command-apps.md) With one command and no @app.callback(), Typer drops the subcommand name.

## map

Orientation.

- [module-map](module-map.md) One line per module and data file, in dependency order.
- [repo-conventions](repo-conventions.md) Writing style, commit rules and test discipline; CLAUDE.md is the source of truth.
