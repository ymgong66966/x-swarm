# x-swarm

Agent swarm that reads the ML frontier every day, forms grounded takes, draws the visual,
queues the post, and learns from what performed. All nine agents are implemented.

```
Scout ─► Curator ─► Analyst ─► Writer ─► Editor ─► Visualizer ─► [review] ─► Publisher
  │         │          │         │         │           │                       │
sources  scoring    grounded  3 variants blocks    renders the                queues in
  (5)   + novelty    brief    per brief ungrounded card + alt text            Typefully
                                                                                 │
              Strategist ◄──────────── Measurer ◄─────────────────────────────────┘
            rewrites playbook.md      X analytics per post
```

The loop closes: the Writer reads `playbook.md` on every run, and the Strategist rewrites
`playbook.md` from measured performance. Everything downstream of Scout is idempotent and
stored in Postgres (SQLite by default for local runs), so any stage can be rerun or replaced
without redoing the others.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/xswarm run --dry-run      # full pipeline, no model spend, no credentials needed
.venv/bin/xswarm review             # drafts waiting on you
.venv/bin/xswarm approve 3          # or: approve 3 --reject --reason "too hypey"
.venv/bin/xswarm publish --dry-run  # what would be queued, and when
```

`--dry-run` skips every LLM call. The Curator falls back to keyword relevance, the Analyst
emits a brief with all claims marked unverified, and the Writer assembles variants from the
brief fields directly — so the graph, the DB, and the editor gate are all exercised for free.

## Commands

| Command | What it does |
|---|---|
| `xswarm init` | Create the schema |
| `xswarm scout` | Ingest from all sources (`--source arxiv` to limit) |
| `xswarm curate` | Score items, shortlist today's candidates |
| `xswarm draft` | Brief → variants → editor gate |
| `xswarm run` | All of the above via the LangGraph pipeline, plus visuals |
| `xswarm review` / `approve` | Human-in-the-loop queue |
| `xswarm render --draft-id 7` | Re-render one draft's visual |
| `xswarm publish` | Queue approved drafts in Typefully at the next free slots |
| `xswarm sync-metrics` | Pull X analytics for published posts |
| `xswarm strategy` | Aggregate performance and rewrite `playbook.md` |
| `xswarm stats` | Row counts |

## Sources

| Source | Auth | Notes |
|---|---|---|
| arXiv API | none | Throttled to 1 req / 3 s per their terms; refreshes once daily at midnight ET |
| HF Daily Papers | optional `HF_TOKEN` | Community upvotes = best free relevance signal |
| Semantic Scholar | optional key | Citation counts + author h-index; 1 rps with a key |
| Lab blogs / newsletters | none | RSS; announcements that never reach arXiv |
| GitHub | optional token | Fast-rising repos + releases of watched repos |

Items found through several sources collapse to one row by arXiv id (or normalized title), and
their signals are unioned.

## Configuration

Copy `.env.example` to `.env`. Every setting is prefixed `XSWARM_` and every one has a default,
so the pipeline runs with an empty `.env`.

The agents run on either OpenAI or Anthropic: set `XSWARM_OPENAI_API_KEY` or
`XSWARM_ANTHROPIC_API_KEY`. With both present OpenAI is used; pin one with
`XSWARM_LLM_PROVIDER=openai|anthropic`. With neither, every model call returns `None` and the
agents take their deterministic fallback paths — the same behaviour as `--dry-run`.

Two files are meant to be edited by hand and by agents:
- `voice.md` — the Writer's few-shot voice card. **Replace the placeholder with real writing
  samples**; this is the single biggest lever on output quality.
- `playbook.md` — learned posting rules. The Strategist rewrites this weekly, and each rewrite
  is a commit, so the file's history is the account's learning history.

## Visuals

The Visualizer picks one of five templates and supplies only data; the renderer
(`src/xswarm/render.py`) draws it. A model cannot emit drawing code, so it cannot draw a
misleading chart — and when the abstract does not contain two comparable numbers, the spec is
rejected and the post falls back to a typographic card.

| Template | Used when |
|---|---|
| `result_chart` | ≥2 comparable numbers on one scale; the paper's method is highlighted |
| `comparison_table` | Before/after or method-vs-method, 2–6 rows |
| `concept_diagram` | The paper's pipeline, 2–5 stages |
| `number_card` | One headline metric |
| `quote_card` | A take with no number behind it |

Cards render to `assets/` (gitignored) at 1600×900 on a dark background, one per brief, only
for drafts that already cleared the Editor. Alt text is derived from the spec, not the model,
so it always describes what was actually drawn.

## Publishing

`xswarm publish` sends **approved** drafts to Typefully as `plan_at` drafts: they sit on the
queue at a real time but never go out unattended. Drop `--schedule-only` once you trust the
output, or list pillars in `XSWARM_AUTOPUBLISH_PILLARS` to let low-risk curation posts skip
review. Slots come from `XSWARM_PUBLISH_SLOTS` with ±7 min of jitter, skipping any slot within
45 minutes of something already queued. The link reply is posted as the second post in the
thread. Without `XSWARM_TYPEFULLY_API_KEY` the Publisher records intent and stops.

## Measurement and learning

`xswarm sync-metrics` pulls per-post X analytics from Typefully and stores a **time series** of
snapshots, so posts can be compared at the same age rather than by whichever has been up
longest. `xswarm strategy` aggregates the latest snapshot per post by pillar, hook style,
visual template, and posting hour, then has the model rewrite `playbook.md` — capped at three
changes per week, and only where a dimension has ≥3 posts behind it. Each run also archives the
raw aggregate to `strategy/<date>.md`. In CI the rewrite opens a PR instead of committing to
main, because that file steers every future post.

## Design notes

- **Grounding is enforced, not requested.** The Analyst separates `grounded_claims` from
  `unverified_claims`; the Editor mechanically blocks any number that does not appear in the
  brief, plus URLs in the body, hashtags, banned hype phrases, near-duplicates, and missing alt
  text. Only drafts that clear the free checks cost a critic call.
- **Links live in a trailing reply.** They suppress reach, and through the X API a post with a
  URL costs ~13x a post without one.
- **No automated replies, likes, or follows.** Since Feb 2026 X rejects programmatic replies
  unless the original author mentioned or quoted you. Automation writes posts; relationships
  stay human.
- **Dry-run fallbacks everywhere.** Every agent degrades to a deterministic path rather than
  failing, which is what makes the whole graph testable without a key.
- **The model never draws.** It fills a validated spec; templates do the rendering. Same idea
  as the claim gate: constrain the output shape so the failure modes are visible.

## Workflows

`ci/workflows/` holds `ci.yml`, `daily.yml`, and `weekly-strategy.yml`. **They must be moved to
`.github/workflows/` to run** — they were parked here because pushing them requires the
`workflow` OAuth scope. Move them with a normal commit from your own machine, or paste them
into the GitHub UI.

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```
