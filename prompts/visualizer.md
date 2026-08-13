Design one visual to attach to this post. You choose the template and supply the data; the
renderer draws it. You cannot draw anything the templates below do not support.

TEMPLATES: {templates}
Preferred (from the brief, override it only if the data does not fit): {preferred}

POST
{body}

BRIEF
What's new: {whats_new}
What it replaces: {what_it_replaces}
Key number: {key_number}
Caveat: {caveat}
Claims you may make:
{grounded_claims}

SOURCE ABSTRACT (the only place numbers may come from)
{summary}

RULES
- Never invent a number, a baseline, or a label. If the abstract does not state two comparable
  numbers, do not use `result_chart` — use `number_card` or `quote_card`.
- `result_chart` needs >= 2 series entries with real values on the same scale and the same unit.
  Mark the paper's own method with "highlight": true.
- `comparison_table` needs 2-3 columns and 2-6 rows, cells under 30 characters.
- `concept_diagram` needs 2-5 stages naming the actual pipeline in the paper. Each stage is a
  label, not a sentence: under 24 characters, no parentheticals.
- `number_card` leads with the single headline metric; `number` is the bare figure (e.g. "29.5%").
- Titles under 60 characters. No hype adjectives. No hashtags.

Reply with a JSON object only, omitting the fields your template does not use:
{{"template": "", "title": "", "subtitle": "", "unit": "", "number": "", "body": "",
  "caption": "", "series": [{{"label": "", "value": 0, "highlight": false}}],
  "columns": [], "rows": [[]], "stages": []}}
