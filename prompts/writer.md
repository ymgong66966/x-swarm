Write {variants} variants of a single X post from this brief.

VOICE (imitate this — it is the account owner's actual writing):
{voice}

CURRENT PLAYBOOK (learned from analytics; follow it):
{playbook}

RECENTLY POSTED (do not reuse these openings, structures or framings):
{recent}

Pillar: {pillar}

BRIEF
What's new: {whats_new}
What it replaces: {what_it_replaces}
Key number: {key_number}
Caveat: {caveat}
Builder takeaway: {builder_takeaway}
Claims you may make:
{grounded_claims}

RULES
- Max {max_chars} characters. Shorter usually wins.
- The first 8 words must earn the scroll-stop. No throat-clearing, no "Interesting paper:".
- Say something. A summary with no opinion is a wasted post.
- Never state a number that is not in the brief. Never name a method the brief did not name.
- No hashtags, no emoji-as-punctuation, no "🚨", no "a thread 🧵", no em-dash pileups.
- No links — the link is posted as a reply automatically.
- Plain, technical, confident. Contractions are fine. Sentence fragments are fine.
- Each variant uses a different hook_style: "claim" (assert the takeaway), "number" (lead with
  the key metric), "contrarian" (lead with the caveat or what everyone gets wrong).
- `alt_text` describes the planned visual for screen readers in one sentence.

Reply with a JSON array only:
[{{"body": "", "hook_style": "claim", "alt_text": ""}}]
