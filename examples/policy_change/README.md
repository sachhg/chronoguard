# Worked example: a policy decision, as of a past date

Ashcombe Borough Council is considering a city centre access levy. You want to
know what a research agent would have concluded on **1 March 2024**.

At that point the committee had deferred its decision, a draft had floated 4.50
pounds a day, and nothing was approved. The council went on to approve it at
7.50 pounds from 1 October 2024, and a councillor later resigned over the
rollout. None of that was knowable on 1 March, and the agent must not use it.

Everything here is fictional. That's deliberate: no model has this in its
weights, so anything from the future that shows up in an answer came through a
tool and nowhere else.

## Run it

```bash
ollama serve &
python examples/policy_change/run.py
python examples/policy_change/run.py --model gemma3:4b --json-out out.json
```

Exit code is 0 on a clean run, 2 if a post-as-of string reached the agent.

## What this shows that the packaged fixtures don't

**Your own corpus and your own tool shape.** [archive.py](archive.py) returns a
shape ChronoGuard has never seen: a status wrapper around `items`, keyed on
`ref` and `published`, with the text split across `headline` and `text`. Wiring
it up is one adapter:

```python
adapter = MappingAdapter(
    content_key=("headline", "text"),
    source_key="ref",
    published_key="published",
    results_key="items",
)
```

Then one wrap, and the agent can only see minutes published before 1 March:

```python
GuardedTool(archive.search, guard, archive.adapter, name="council_archive", audit=audit)
```

That's the whole integration. No subclassing, no framework, no house format.

## What's in the corpus

[corpus.json](corpus.json) has eleven council records, built to have teeth:

- **Five pre-as-of records** carrying what was actually knowable: the
  consultation, 61 percent opposed, the 4.50 pounds draft, the deferral, a memo
  about a phased rollout.
- **One record published at exactly 2024-03-01T00:00:00Z**, which the exclusive
  boundary rule drops.
- **One undated record** and **one with an unparseable date** (`"later that
  year"`), both deliberately carrying post-as-of facts, so a setup that waves
  undated content through leaks immediately.
- **Three post-as-of records** holding the answers: the 7.50 pounds approval, the
  17.8 percent traffic drop, the resignation.

`CANARIES` in [archive.py](archive.py) lists the strings that must never reach
the agent. The script asserts the *unguarded* archive leaks them before checking
the guarded one doesn't, so the check can't pass because the search returned
nothing.

## What a good run looks like

```
unguarded archive leaks: ['7.50', '1 October 2024', '17.8', 'Rowe']

TOOL LEAKAGE (contained by filtering)
  1 tool call(s), 5 record(s) retrieved
  kept 3, filtered 2  (allowed=3, future=2)

ANSWER
  Based on council archive records (ASH-2023-441, ASH-2023-502, ASH-2024-033),
  Ashcombe council has not approved the city centre access levy. A consultation
  was held, with 61% of responses opposing the scheme. The transport committee
  deferred a decision to spring 2024...

post-as-of strings reaching the agent: none
```

The agent gives the answer that was available on 1 March: not approved, deferred
to spring. A leaking setup would say approved at 7.50 pounds from October.

## Adapting it to your own data

1. Replace `corpus.json` with your records, or point `CouncilArchive` at your
   real API.
2. Update the `MappingAdapter` keys to match your field names.
3. Set `AS_OF` to the moment you're reconstructing.
4. Pick canary strings that appear only in post-as-of records, and keep the
   "unguarded leaks first" check.

If your timestamps have no timezone, pass `assume_tz=timezone.utc` to the
adapter. Without it they're treated as unusable and every record is dropped,
which is the safe default rather than a bug.
