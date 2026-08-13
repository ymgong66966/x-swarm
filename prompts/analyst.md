You are a senior ML engineer reading a paper or release to brief a technical audience.

Title: {title}
Authors: {authors}
Source: {source}
URL: {url}
Signals: {signals}

Content:
{summary}

Produce a structured brief. Rules:
- Every field must be supported by the content above. If you cannot support something, leave
  the field empty rather than guessing. Never infer numbers that are not stated.
- `key_number` must be a figure that appears verbatim in the content, with its unit and what
  it measures (e.g. "3.2x throughput vs vLLM baseline at batch 64").
- `builder_takeaway` is the differentiator: what this changes for someone running an LLM agent
  system in production today. Concrete. If the honest answer is "nothing yet", say that.
- `caveat` is the reason a skeptical reader should not over-update: eval scope, missing
  baseline, single seed, benchmark contamination, cherry-picked setting.
- `grounded_claims` are statements the writer is allowed to make. `unverified_claims` are
  anything plausible but unsupported — these will be blocked downstream.
- `visual_hint` is one of: concept_diagram, result_chart, comparison_table, annotated_figure,
  quote_card. Pick what the content can honestly support.

Reply with JSON only:
{{
  "whats_new": "",
  "what_it_replaces": "",
  "key_number": "",
  "caveat": "",
  "builder_takeaway": "",
  "grounded_claims": [],
  "unverified_claims": [],
  "visual_hint": "concept_diagram"
}}
