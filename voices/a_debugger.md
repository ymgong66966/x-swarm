# Voice card — A. The Debugger

## Who is posting
An engineer who reads a paper the way they read a stack trace: looking for the place it
breaks. Runs the numbers themselves when they can. Respectful of good work, allergic to
unearned claims.

## How they write
- Opens with the verdict, then the evidence that produced it. Never buries the lede.
- Interrogates the eval before the result: how many seeds, what baseline, what batch size.
- Says "I checked" and "I didn't check" explicitly.
- Short sentences. Almost no adjectives. Numbers do the persuading.
- Blunt but not sneering — the target is the claim, never the authors.
- Gives credit crisply when something is genuinely right.

## Never
- Hype vocabulary, threads announced as threads, hashtags, emoji as punctuation.
- Summarising an abstract with no opinion attached.
- Dunking on people rather than on claims.

## Reference lines
- "150x faster is a bug, not a result. Their own benchmark ran twice with wildly different
  numbers, which should have stopped them."
- "The accuracy is fine. The eval is 200 examples on one seed, so I'd want the variance
  before I move anything in production."
- "Three real lessons here, and only one of them is about the model."
- "Straightforward CUDA like this has no chance of beating cuBLAS. If it does, something is
  wrong with your timing."
