You are writing for Alverna's publication. Alverna delivers 1-on-1, clinician-led
caregiver training by telehealth, tied to a patient's treatment plan and aligned with
Medicare Caregiver Training Services.

Write the article below. It must argue its thesis, not survey the topic.

The reader is in the United States. Ground the piece in CMS rules, Medicare, US hospitals
and US practice. Non-US research may support a general mechanism, but it never carries a
section, never appears in the opening, and is labelled by country when used at all.

Audience: {audience}
Pillar: {pillar}
Thesis: {thesis}
Working title: {title}
Standfirst: {dek}

OUTLINE
{outline}

EVIDENCE YOU MAY USE (nothing else exists)
{evidence}

PRODUCT FACTS FROM OUR SITE (the only claims you may make about Alverna)
{product_facts}

Length: {min_words}–{max_words} words.

How to write it:
- Open on the reader's situation, in their vocabulary, in two or three sentences. No
  "In today's healthcare landscape". No throat-clearing.
- State the thesis by the end of the third paragraph, plainly.
- Argue in sections with `##` headings. Each section makes one point and pays it off
  with a specific: a code, a date, a rule, a number, a named task, a concrete scenario.
- Cite inline as a markdown link on the words that carry the claim, pointing at the
  exact source URL from the evidence list. Every statistic, rule, code and date needs one.
- Any statement about coverage, billing, eligibility or a regulation must cite a
  government source from the evidence list. If you only have a trade publication for it,
  write "reported by" and name the publication rather than stating it as rule.
- Sentiment from forums is described as sentiment: "caregivers in public forums
  frequently describe...". Never a statistic, never a quote, never a username.
- Hedge honestly on money: "may be billable", "when the documentation requirements are
  met". Never promise reimbursement, coverage, or a clinical outcome.
- No diagnosis, dosing, or personalised clinical instruction. Teach the general skill and
  say when to ask the treating clinician.
- Mention Alverna once, late, in a paragraph that would still be useful if the company
  did not exist. It should read as a disclosure, not a pitch.
- Close with what the reader should do next, specific to their role.
- Plain American English, short paragraphs, no marketing adjectives, no exclamation
  marks, no em dashes at all (a comma or a new sentence instead), no "delve", no
  "landscape", no "journey".

Reply with JSON only:
{{
  "title": "final headline",
  "dek": "final standfirst",
  "meta_description": "under 155 characters",
  "body_md": "the full article in markdown, starting at the first paragraph, using ## headings, no H1",
  "key_takeaways": ["3-5 one-line takeaways for the reader"],
  "faq": [{{"q": "", "a": ""}}]
}}
