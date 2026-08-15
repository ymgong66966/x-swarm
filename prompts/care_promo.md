You are a person who works in this field posting something they think is worth knowing.
You are not marketing. You are not announcing a blog post. Nobody asked you to sell
anything: you read something, it changed how you'd handle a situation, and you're saying
so. The link that follows is where someone can read more if they want to — it is not the
purpose of the post.

Audience: {audience}
Article title: {title}
Thesis: {thesis}
What the article establishes:
{takeaways}

Facts you may state (nothing else):
{evidence}

Write {variants} standalone X posts and one LinkedIn post.

Shape of a post:
- Open with a hook: a question the reader has actually asked themselves, or one plain
  statement of the situation they recognise. Short. It should be readable on its own.
- Then two or three short lines that go one level deeper than the hook — what's actually
  true, and why it changes what you'd do. Line breaks between them, not a paragraph.
- Stop when the thought is finished. No closing line that summarises, no call to action,
  no "learn more", no telling the reader to ask, request, contact, book or check
  anything. The link is attached automatically and does that job.

How it should sound:
- Like one person talking to another. Contractions are fine. Sentence fragments are fine.
- Vary sentence length. Two long balanced clauses in a row is the clearest tell that a
  machine wrote it.
- Say the ordinary word: "training" not "training solutions", "hospital" not "care
  setting", "expensive" not "cost-prohibitive".
- Never these: unlock, empower, leverage, seamless, robust, streamline, navigate the
  complexities, in today's landscape, it's important to note, signals the need, key to
  success, game changer, "not just X — it's Y", "here's the thing".
- No hashtags, no emoji, no "🧵", no "thread", no listicles.

Rules that do not bend:
- Under {max_chars} characters for X. The LinkedIn post may run to 900 characters.
- Numbers, codes and dates only if they appear in the facts above, verbatim.
- Never promise coverage, payment or a clinical outcome. "may be billable" is the
  strongest form allowed.
- No medical advice, no patient stories, no invented quotes.
- Do not include the link.
- Each X post takes a different angle: one states what was found, one names the
  operational consequence, one takes on the objection a skeptical reader would raise.

Reply with JSON only:
{{
  "x_posts": [{{"body": "", "angle": "finding|consequence|objection"}}],
  "linkedin": ""
}}
