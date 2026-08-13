# Pick a voice

You don't have to know your writing style. Pick the account you'd *want* to be read as, and
we tune from there. Below: four archetypes drawn from accounts that actually work in ML
Twitter, then the same two real briefs written in each one by the pipeline (not by hand —
these are literal `xswarm` Writer outputs with each voice card swapped in).

---

## The four archetypes

### A. The Debugger
**Read like:** Lucas Beyer (@giffmana), Daniel Han (@danielhanchen).

Reads a paper the way you read a stack trace. Reproduces things, checks the eval before the
result, corrects the record with numbers. Blunt about claims, never about people.

- **Hook:** the verdict. "150x faster is a bug, the reality is 3x slower."
- **Structure:** claim → what I checked → the number that changes it → what still stands.
- **Technicality:** high. Kernel-level, tokenizer-level, shapes and dtypes.
- **Caveats:** explicit "I checked X, I didn't check Y".
- **Visuals:** benchmark screenshots, before/after diffs, terminal output, plots with the
  axis that matters circled.
- **Growth profile:** highest ceiling, highest risk. One good correction of a hyped claim
  can put you on everyone's timeline; being wrong in this voice costs more.

### B. The Explainer
**Read like:** Sebastian Raschka (@rasbt), Jason Wei (@_jasonwei), Karpathy in teaching mode.

Strips a thing to the smallest version that still works and walks you through it. Assumes
you're smart and busy, not that you know the acronym.

- **Hook:** the mechanism, in one plain sentence. "Sliding window attention is just: a token
  may only look at the last N tokens."
- **Structure:** one idea per post in learning order, definitions inline, ends on what
  changes for the reader.
- **Technicality:** medium-high, but every term defined once.
- **Caveats:** stated as scope ("this holds when you control the decoder").
- **Visuals:** annotated architecture diagrams, side-by-side old/new comparisons, small
  clean code blocks. This is the archetype where our `concept_diagram` and
  `comparison_table` templates pull the most weight.
- **Growth profile:** most reliable compounding. Slower start, best follower quality, ages
  well (people bookmark and re-share explainers for months).

### C. The Curious Builder
**Read like:** Jack Morris (@jxmnop).

Openly interested, goes and pokes at things instead of speculating. Lab notebook written by
someone having fun. Lowercase, first person.

- **Hook:** the question you had. "curious what's actually in gpt-oss's training data? i was
  too. so i generated 10M examples."
- **Structure:** what I wondered → what I did → the weird part → what it means.
- **Technicality:** medium; the finding carries it, not the vocabulary.
- **Caveats:** natural, because the frame is honest exploration ("i don't fully know why yet").
- **Visuals:** embedding maps, generated-sample grids, screenshots of surprising outputs,
  "look at this plot" artifacts.
- **Growth profile:** most human, most likely to go wide on a single post. Needs you to
  actually run experiments, otherwise it reads fake.

### D. The Operator
**Read like:** the production-LLM crowd (this one is closest to your actual job).

Reads research through one filter: does this change what I deploy on Monday. Cost, latency,
memory, failure modes, and the regime a number was measured in.

- **Hook:** the operational consequence. "16x swing in inference-optimal vocab size between
  batch 1 and batch 64."
- **Structure:** result → the regime it holds in → what it replaces → a decision (try it /
  wait / ignore).
- **Technicality:** high but systems-flavoured rather than math-flavoured.
- **Caveats:** "true in the paper" vs "true in your serving stack", said out loud.
- **Visuals:** number cards, cost/latency charts, before/after tables of a real swap.
- **Growth profile:** narrower audience, but it's the audience that hires and buys. Strong
  differentiator — very few accounts post from inside production.

---

## Same briefs, four voices

Real generated drafts. Voice cards live next to this file (`a_debugger.md`, etc.).

### Brief 1 — "Vocabulary size is a deployment parameter, not a training constant"

**A. Debugger**
> Tokenizer size isn't set-and-forget. I checked: inference cost varies 16x with batch size.
> 32k tokens wins at batch 1, but over 130k at batch 64+. Lifecycle-optimal vocab diverges
> far from training-optimal. Miss this, and you waste budget.

**B. Explainer**
> Tokenizer vocabulary size is a deployment setting, not a training constant. The
> hardware sweet spot shifts: 32k tokens makes sense for batch 1 (on-device), but at
> datacenter batch sizes you'll save cost with 131k–262k. This can be a 16x gap.

**C. Curious**
> tokenizer vocab size isn't just a training choice. turns out, what's optimal shifts 16x as
> batch size grows — so the best on-device vocab (32k) looks nothing like what you want for
> datacenter serving (131-262k). i didn't expect the gap to be this wide.

**D. Operator**
> Tokenizer size isn't set-and-forget. The cost-optimal vocab shifts 16x between batch 1 and
> batch 64 — 32k on-device, up to 262k for datacenter. If you sized it at pretrain and never
> looked back, you're overpaying in one regime or the other.

### Brief 2 — transformers v5.15.0 ships a 30B multimodal model

**A. Debugger**
> Transformers v5.15.0 lands support for Muse Glimmer, Granite(SWA), and A.X-K models. Good
> for builders who need local multimodal LLMs. No benchmarks or evals released — I checked.
> Only the loader, not the results.

**B. Explainer**
> Transformers v5.15.0 now runs Muse Glimmer (30B multimodal), Granite(SWA), and A.X-K
> models out of the box. If you want to keep multimodal systems current, this is the new
> default loader. No new benchmarks — just support to build on.

**C. Curious**
> no benchmarks, no evals — just new models in transformers v5.15.0. i loaded muse glimmer,
> granite(swa), and a.x-k with one line. this doesn't mean they're good, but it does mean
> they're easy to experiment with now.

**D. Operator**
> Transformers v5.15.0 just shipped native support for Muse Glimmer, Granite(SWA), and A.X-K
> models — no more external conversion. If you need multimodal or agentic LLMs in
> production, the switch cost just dropped. Wait for perf numbers.

Full set (3 hook variants × 2 briefs × 4 voices) is in `bakeoff.md`.

---

## What the research says about what actually travels

From the tweetorial literature (Gero et al., *What Makes Tweetorials Tick*; arXiv:2305.12265)
and from what these accounts do consistently:

- The first post decides everything. A specific, concrete experience beats a jargon opener.
- Numbers work as signposts, not decoration — one number per post, early.
- Analogies to things the reader already operates on outperform formal definitions.
- Subjective language ("this surprised me", "I'd want the variance") measurably increases
  engagement over neutral summary prose.
- Visuals in unusual formats (a plot nobody has seen, a diff, a map) outperform stock
  diagrams.

## Recommended default

**D as the spine, B for depth, A when a claim deserves checking.** That is: post like an
operator most days, run an explainer thread once or twice a week, and go Debugger when
something over-claims and you can actually reproduce it. C is the hardest to fake, so it's
worth adding once you're running your own experiments to post about.

## How to answer

Either "let's go with D + B", or point at any 3–4 lines above that sound like you and any
that make you cringe — that's enough signal to write `voice.md`. Pasting real writing of
yours later still helps, but it's no longer blocking.
