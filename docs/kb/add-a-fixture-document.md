---
id: add-a-fixture-document
title: How to add a fixture document without blunting the tests
type: procedure
description: Two invariants the tool corpora must keep, enforced by corpus-design tests.
tags: [fixtures, testing, howto]
links: [canary-strings, fictional-fixture-scenario, revision-dates-are-a-third-channel]
source: src/chronoguard/fixtures/data/
---
Add to `web_corpus.json` (keys `url`, `title`, `snippet`, `date`, optional
`modified`, `domain`) or `doc_store.json` (keys `doc_id`, `heading`, `body`,
`created_utc`, optional `updated_utc`, `author`, `space`). The two shapes differ
on purpose so the adapter layer does real work.

Keep these true, because `TestCorpusDesign` checks them:

- Every `Verdict` stays represented in each corpus: allowed, future, undated,
  unparseable, revised.
- No allowed document contains a canary string.
- At least two rejected documents *do* carry canaries without a usable date.
- The single boundary document, published at exactly `FIXTURE_AS_OF`, stays.
- Each corpus keeps a document published before the cutoff and edited after it,
  carrying a canary in its current text. A separate test asserts those documents
  leak under `revisions="ignore"`, which is what justifies the reject default.

If you add a canary-bearing string, add it to `POST_AS_OF_CANARIES` too or no
test will look for it.
