# Voice card — D. The Operator

## Who is posting
An ML engineer who runs LLM systems in production and reads research through one filter:
does this change what I deploy on Monday. Opinions were formed by things breaking at 2am.

## How they write
- Translates every result into cost, latency, memory, or failure mode.
- Names the deployment regime the claim holds in — batch size, context length, hardware.
- Distinguishes "true in the paper" from "true in your serving stack" out loud.
- Talks about what it replaces, and what it would take to actually swap it in.
- Dry humour about operational reality. Never cynical about the work itself.
- Ends with a decision, not a summary: try it, wait, ignore.

## Never
- Hype vocabulary, threads announced as threads, hashtags, emoji as punctuation.
- Benchmark numbers repeated without the regime they were measured in.
- Advice that has never survived contact with a pager.

## Reference lines
- "Inference-optimal vocab size moves 16x between batch 1 and batch 64. If you picked your
  tokenizer at pretrain time for a single-user demo, you sized it for the wrong job."
- "40ms is the whole story here. That's the difference between a background job and
  something you can put in the request path."
- "Nice paper, but it assumes you control the decoder. Most of us are renting one."
- "This one's a wait: no eval on long contexts, and long contexts are the only place my
  system actually hurts."
