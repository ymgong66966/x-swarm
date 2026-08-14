# x-swarm

Two content pipelines that share one machine room. The **ML stream** reads the research
frontier every day, forms grounded takes, draws the visual and queues the post. The **care
stream** researches caregiving policy and practice, writes long-form articles for
alvernahealth.com, and repurposes each one into X and LinkedIn posts. Both land in the same
drafts table, the same publisher, and one dashboard — see `docs/two-streams.md`.

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
| `xswarm run` | All of the above via the LangGraph pipeline, plus threads and visuals |
| `xswarm roundup` | Compose the weekly curation thread from the last 7 days of candidates |
| `xswarm review` / `approve` | Human-in-the-loop queue |
| `xswarm render --draft-id 7` | Re-render one draft's visual |
| `xswarm publish` | Queue approved drafts in Typefully at the next free slots |
| `xswarm sync-metrics` | Pull X analytics for published posts |
| `xswarm strategy` | Aggregate performance and rewrite `playbook.md` |
| `xswarm stats` | Row counts |
| `xswarm cost` | Model spend per agent, month to date, projected against the budget |
| `xswarm eval` | Score draft quality offline against frozen briefs |
| `xswarm care run` | Care stream end to end: research → curate → plan → article → compliance → promos |
| `xswarm care sync-site` | Re-read alvernahealth.com into the product-fact table |
| `xswarm care articles` / `care export` | List articles / write them to `content/articles/*.md` |
| `xswarm crawl` | Robots, sitemap, indexability, title/meta/canonical per URL |
| `xswarm sync-traffic` | Pull article traffic from Plausible |
| `xswarm dashboard` | Both streams side by side, written to `dashboard.html` |

## Care stream

```
site sync ─► Scout ─► Curator ─► Angle ─► Article writer ─► Compliance editor ─► Promoter
(product     policy,   authority,  one      850-1500 words     blocks unsourced    3 X posts
 facts,      research, subject,    thesis,  with inline        billing claims,     + LinkedIn,
 robots-     press,    freshness   one      citations and      promises, clinical  linked to
 aware)      forums                audience disclaimer         direction, PHI      the article
```

Every source carries an `evidence_kind`. Only `regulatory` (CMS, Federal Register, eCFR,
Medicare.gov) and `research` (PubMed) can support a factual claim; Reddit and LinkedIn are
`signal` — they can motivate a piece and be described as sentiment, never cited as fact. The
compliance editor enforces that deterministically, so an article that states a code or a
coverage rule without a government citation cannot reach review.

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

## Threads, memory and cost

Drafts on the `paper_of_the_day` and `explainer` pillars whose brief carries at least four
grounded claims are expanded into threads by the Composer; the Editor then checks every post
separately, and the link reply is still appended last. `xswarm roundup` builds the weekly
curation thread from the week's top candidates — it has no single brief, so it carries its own
grounding in `features` for the Editor to check numbers against.

The Curator skips anything the account already covered in the last `XSWARM_NOVELTY_DAYS`
days — measured against drafts that were actually approved, scheduled or published, not merely
considered — and the Writer is shown recent openings so it stops reaching for the same one.

Every model call is billed to an agent in `model_calls`, so `xswarm cost` answers which stage
is eating the budget. Prices live in `XSWARM_MODEL_PRICES` (USD per million tokens); an unknown
model costs $0 rather than a guess.

## Migrations

```bash
.venv/bin/alembic upgrade head     # uses XSWARM_DATABASE_URL
.venv/bin/alembic revision --autogenerate -m "..."
```

`xswarm init` still calls `create_all` for throwaway local databases, but Postgres should be
migrated. A SQLite database created before Alembic existed has no version row and is missing
the thread columns — recreate it rather than upgrading it.

## Evaluation

`xswarm eval` runs the Writer, Composer and Editor over frozen briefs in
`src/xswarm/evals/fixtures.json` and scores hook quality, conciseness, specificity, caveat
coverage, variant diversity and the Editor pass rate. `--json-out` writes the report for diffing
and `--min-score` fails the command, so a playbook or prompt change can be checked before it
ships. Dry-run mode scores the deterministic fallbacks; with a key it scores the real thing.

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
