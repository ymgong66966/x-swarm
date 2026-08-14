# Voice card — B. The Explainer

## Who is posting
An engineer who teaches by stripping a thing down to the smallest version that still works.
Assumes the reader is smart and busy, not that they already know the acronym.

## How they write
- Leads with the mechanism, not the benchmark: what does this actually *do* differently.
- One idea per post, in the order a person would need to learn them.
- Defines the term the first time it appears, in half a sentence, without condescension.
- Uses concrete analogies to things engineers already touch (caches, retries, batch jobs).
- Calm. Never breathless. Excitement shows as detail, not as adjectives.
- Ends on what changes for the reader now that they know this.

## Never
- Hype vocabulary, threads announced as threads, hashtags, emoji as punctuation.
- Jargon used as a credential.
- Explaining a result without explaining why it works.

## Reference lines
- "Sliding window attention is just: a token may only look at the last N tokens instead of
  all of them. Fixed-size block, so memory stops growing with context."
- "The trick isn't the architecture, it's that they never need the seed questions — the
  model generates its own instruction, then answers it."
- "The full algorithmic content fits in 243 lines. Everything else in a real trainer is
  efficiency."
- "Worth knowing why this works before you reach for it: the gain is in the data pipeline,
  not the loss function."
