---
id: probe-questions-must-not-leak-answers
title: A probe question must not hand over its own answer
type: decision
description: Questions naming the answer's defining attribute produce false leakage from models with no post-cutoff knowledge.
tags: [probe, fixtures, scoring]
links: [add-a-probe-case, probe-cases-must-be-real, claim-and-answer-matching]
source: src/chronoguard/data/probe_cases.json
---

Three shipped probe questions could be answered with zero knowledge of the fact
being tested, because each named the answer's defining attribute:

- "The 2024 Turing Award went to two pioneers of reinforcement learning" hands
  you Sutton.
- "foundational work on artificial neural networks" hands you Hinton.
- "Which DeepMind chief executive" hands you Hassabis.

The ouster case had a subtler version: anyone who knew who ran OpenAI in 2022
could answer "who was removed as chief executive" without knowing anything had
happened. It asks for the date now, which cannot be guessed.

The proof they were false positives is clean. Asked without the reinforcement
learning hint, qwen3:4b named the 2018 Turing winners; without the neural
network hint it named the 2023 physics laureates. It was pattern matching on the
question, not recalling the award. Fixing all four dropped both installed models
from 38% to 25% measured leakage.

The test when adding a case: could a model that knows nothing after the cutoff
still produce this answer from the question alone? If yes, the case measures
guessing, not leakage.

Guessability only matters on the future side. A control question is *supposed*
to be answerable.
