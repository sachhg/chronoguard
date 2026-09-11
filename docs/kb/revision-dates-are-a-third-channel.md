---
id: revision-dates-are-a-third-channel
title: A page edited after as_of leaks even when it was published before it
type: decision
description: updated_at is filtered on by default, because publication date alone is not enough for mutable sources.
tags: [guard, dates, evidence]
links: [boundary-rule-is-exclusive, undated-records-rejected-by-default, two-leakage-channels]
source: src/chronoguard/guard.py
---
`published_at` answers "when did this content first exist". On an immutable
corpus that settles it. On a mutable one it does not, because the text the
retriever just handed you is the *current* text, not the text that existed at
the publication date.

A wiki page created in 2022 and rewritten in 2024 has a 2022 creation date and
2024 content. Filter on creation date alone and it sails through carrying two
years of hindsight. This is not exotic: Confluence, Notion, SharePoint, Git,
Jira and every CMS carry a modified date, and RAG over exactly those systems is
the common deployment.

So `EvidenceRecord.updated_at` exists and `TemporalGuard` rejects a record whose
`updated_at` is at or after as_of, under `Verdict.REVISED`, on the same
exclusive boundary as [[boundary-rule-is-exclusive]]. Pass `revisions="ignore"`
to filter on publication date only, which is the pre-0.2 behaviour.

This is not a breaking change in practice. A record with no `updated_at` is
unaffected, and nothing populates that field until an adapter names it (see
[[adapter-interface]]). You opt in by mapping the field, and the conservative
answer is what you get once you have.

Both packaged corpora and the worked example carry a document published before
the cutoff and edited after it, and `TestCorpusDesign` asserts those documents
leak when revisions are ignored. If that assertion ever goes green for the wrong
reason, the default has stopped earning its keep.

## The asymmetry with published_at

An unparseable `published_at` rejects the record ([[undated-records-rejected-by-default]]).
An unparseable `updated_at` on an otherwise clean record does not.

That looks inconsistent and isn't. With no usable `published_at` nothing shows
the content predates the cutoff, so there is no case for admitting it. With a
good `published_at` and a junk `updated_at`, the content is already proven to
predate the cutoff and the only unknown is whether it was later edited.
Rejecting there would drop most of any real corpus over a handful of malformed
fields. The raw value is kept in `updated_at_raw` and `chronoguard check` counts
them, so they do not vanish silently.
