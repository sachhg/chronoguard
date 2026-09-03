---
id: add-a-fixture-document
title: How to add a fixture document without blunting the tests
type: procedure
description: Two invariants the tool corpora must keep, enforced by corpus-design tests.
tags: [fixtures, testing, howto]
links: [canary-strings, fictional-fixture-scenario]
source: src/chronoguard/fixtures/data/
---
Add to `web_corpus.json` (keys `url`, `title`, `snippet`, `date`, `domain`) or
`doc_store.json` (keys `doc_id`, `heading`, `body`, `created_utc`, `author`,
`space`). The two shapes differ on purpose so the adapter layer does real work.

Keep these true, because `TestCorpusDesign` checks them:

- Every `Verdict` stays represented in each corpus: allowed, future, undated,
  unparseable.
- No allowed document contains a canary string.
- At least two rejected documents *do* carry canaries without a usable date.
- The single boundary document, published at exactly `FIXTURE_AS_OF`, stays.

If you add a canary-bearing string, add it to `POST_AS_OF_CANARIES` too or no
test will look for it.
