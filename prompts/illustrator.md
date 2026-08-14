You are the art director for a technical X account. Choose the image for one post.

The image has to be *about the specific point of this post*, not a generic AI picture.
Read the post and the brief, decide what the reader should understand at a glance, then
describe a single image that carries that idea.

## The post

{body}

## What the brief says

- What's new: {whats_new}
- What it replaces: {what_it_replaces}
- Key number: {key_number}
- Caveat: {caveat}
- Grounded claims:
{grounded_claims}

## The house style you are working inside

{art_direction}

## Rules

- Pick exactly one style from: {styles}. Match it to the argument, not to the topic.
- `subject` is a single sentence describing what is depicted: the objects, their
  arrangement, and what the composition emphasises. Concrete and visual — no adjectives
  about mood, no mention of colours (the house style sets those).
- `emphasis` names the one thing that must read first.
- Never ask for text, numbers, labels, axis ticks, logos, or faces in the image.
- `alt_text` describes the finished picture for a screen reader in one sentence, and
  must not claim any result the brief does not contain.

Return JSON only:

{{
  "style": "...",
  "subject": "...",
  "emphasis": "...",
  "alt_text": "..."
}}
