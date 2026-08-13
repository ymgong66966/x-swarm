Expand this opening post into a thread. The opening post is already written and must not
be repeated — you are writing posts 2..N only.

VOICE (imitate this):
{voice}

OPENING POST (already published as post 1):
{opening}

BRIEF
What's new: {whats_new}
What it replaces: {what_it_replaces}
Key number: {key_number}
Caveat: {caveat}
Builder takeaway: {builder_takeaway}
Claims you may make:
{grounded_claims}

SOURCE ABSTRACT (for accuracy only — do not quote it):
{summary}

RULES
- At most {max_posts} posts. Fewer is better. Cut anything that only restates the opening.
- Each post is at most {max_chars} characters and must survive on its own.
- Every post must add one new thing: a mechanism, a number, a comparison, a limitation.
- Never state a number that is not in the brief. Never name a method the brief did not name.
- One post must be the honest caveat — what this does not do, or where it breaks.
- The last post is the builder takeaway: what a reader should actually do with this.
- No hashtags, no emoji, no links, no "1/", no "🧵", no numbering of any kind.
- At most one em-dash per post; pileups read as LLM output and get blocked.

Reply with a JSON array of strings only:
["", ""]
