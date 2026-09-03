---
id: preamble-filters-eat-real-claims
title: Keep chatter filters narrow
type: pitfall
description: A preamble regex listing 'claims?|answer' silently dropped genuine claims starting with those words.
tags: [claims, parsing, bugs]
links: [claim-label-meanings]
source: src/chronoguard/claims.py
---
`parse_claims` drops model chatter around a list. The first version matched lines
starting with `here|these|below|the following|sure|okay|ok|certainly|claims?|answer`.

"Claim" and "Answer" are ordinary ways to start a real sentence, so a genuine
claim like "Claim number 3 was never substantiated" got silently dropped.

The regex is now `here (are|is)|these are|below|the following|sure|okay|ok|certainly`.

When filtering model output, prefer patterns that cannot match content. Silent
drops are worse than passing chatter through, because chatter gets labelled
BENIGN and a dropped claim gets labelled nothing.
