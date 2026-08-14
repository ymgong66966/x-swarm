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
| `xswarm illustrate --draft-id 7` | Generate house-style art for a draft (`--style` to force one) |
| `xswarm ingest add <link\|file\|text>` | Your own material → thread + visual, held for review |
| `xswarm ingest schedule 12` | Queue an **approved** ingest draft in Typefully |
| `xswarm publish` | Queue approved drafts in Typefully at the next free slots |
| `xswarm sync-metrics` | Pull X analytics for published posts |
| `xswarm strategy` | Aggregate performance and rewrite `playbook.md` |
| `xswarm stats` | Row counts |
| `xswarm cost` | Model spend per agent, month to date, projected against the budget |
| `xswarm eval` | Score draft quality offline against frozen briefs |
| `xswarm care run` | Care stream end to end: research → curate → plan → article → compliance → promos |
| `xswarm care sync-site` | Re-read alvernahealth.com into the product-fact table |
| `xswarm care articles` / `care export` | List articles / write them to `content/articles/*.md` |
| `xswarm care approve 12` | Human gate: mark a reviewed article approved (or `--reject --reason ...`) |
| `xswarm care publish 12 --hero art.png` | Branch + draft PR the approved article into the Alverna site repo |
| `xswarm care sync-edits 12` | Read edits you made in that PR back into the article |
| `xswarm care status` | Every article's state: PR, live URL, promo posts |
| `xswarm care promote 12` | Only once the article URL returns 200: release its promo drafts |
| `xswarm care watch` | Unattended sync-edits + promote for every article waiting on a site PR |
| `xswarm crawl` | Robots, sitemap, indexability, title/meta/canonical per URL |
| `xswarm sync-traffic` | Pull article traffic from Plausible |
| `xswarm dashboard` | Both streams side by side, written to `dashboard.html` |

## Publishing an article

The site repo is the publication gate — nothing goes live without a human merge.

```
care run ─► review ─► care approve ─► care publish ─► draft PR on alverna-site
                                                          │
                                    you edit the markdown in the PR (press `.` on it)
                                                          │
                                              care sync-edits ─► your wording lands in the DB
                                                          │
                            "Ready for review" ─► you merge ─► /resources/<slug> live
                                                          │
                                     care promote ─► URL must return 200 ─► promos approved
                                                          │
                                                    xswarm publish ─► Typefully schedules them
```

The PR is the editing surface, so there is no second CMS to log into and the text you edit is
the text that ships. `care publish` refuses anything that is not `approved`, renders the
article in the site's front-matter dialect into `content/resources/<date>-<slug>.md`, copies
`--hero` to `public/resources/media/<slug>.<ext>`, pushes a branch, and opens a **draft** PR
when `XSWARM_GITHUB_TOKEN` is set (otherwise it prints the compare URL for you to open).
`--ready` skips the draft state and `--dry-run` renders the file and touches no git.

`care sync-edits` reads the file back off the PR branch so the promo posts quote your final
wording rather than the model's first draft.

`care watch` is the same two steps for every pending article at once, with nothing to type per
article: it syncs each one's edits (from the PR branch, or from `main` once the branch is
deleted by the merge) and promotes the ones that are already live. The `care-watch` workflow
(`ci/workflows/care-watch.yml`, to be moved into `.github/workflows/` by hand — my token cannot
write workflow files) runs it hourly and on a `repository_dispatch: article-merged` that alverna-site sends when a
merge to its `main` touches `content/resources/**`, so after you merge the PR the rest of the
loop happens without anyone at a terminal. It needs `XSWARM_DATABASE_URL` to point at the same
(Postgres) database the runs write to — the state cannot live in a local SQLite file if a
runner is meant to advance it.

`care promote` needs more than a 200: the site is a single-page app whose host answers 200
with the same shell for every unknown path, so the page must also mention the article's own
path before the promos are released. If a page you can see in a browser is still refused, the
host is not serving the prerendered head tags — `care promote <id> --force` overrides.

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

### Generated art

When there is no measured number to plot, the **Illustrator** writes an art spec and
`gpt-image-1` draws it. The split is deliberate: anything factual is rendered by matplotlib,
anything conceptual is generated, and a failed generation falls back to a rendered card
rather than shipping a bare post.

`XSWARM_VISUAL_MODE` picks the policy: `auto` (default, generate only where there is no data),
`render` (never generate), `generate` (always try). The house look lives in
`prompts/art_direction.md` — dark `#0d1117`, one blue and one orange accent, flat editorial
illustration, no faces or chrome — and every prompt ends with a hard *no text, no numbers, no
labels, no logos* constraint, because generated type is the fastest way to look fake. Image
models honour that maybe two times in three, so every generated image is read back by the
fast model; one with words in it is retried once and then abandoned for a rendered card.

| Style | Used for |
|---|---|
| `frontier_diagram` | Architectures, mechanisms, how a method works |
| `risk_dark` | Failure modes, over-claims, counterpoints |
| `data_poster` | A measured jump, drawn as poster geometry (the number stays in the text) |
| `clinical_calm` | Care-stream posts: objects and rooms, never medical drama |
| `concept_hero` | Opinion posts, essays, your own work |

```bash
.venv/bin/xswarm illustrate --draft-id 7                       # any draft, any stream
.venv/bin/xswarm illustrate --draft-id 7 --style clinical_calm # force the look
```

Images cost ~$0.063 each and are counted per stream by `xswarm cost` and the dashboard.

## Your own material

A link, an arXiv paper, your blog, a text file, or pasted text — turned into a thread with a
visual, held for review, then queued.

```bash
.venv/bin/xswarm ingest add https://arxiv.org/abs/2404.19756
.venv/bin/xswarm ingest add ~/notes/post.md --image ~/figs/latency.png --alt "Latency chart"
.venv/bin/xswarm ingest add "Some text you just wrote." --no-illustrate
.venv/bin/xswarm review && .venv/bin/xswarm approve 12
.venv/bin/xswarm ingest schedule 12
```

arXiv links and bare ids go through the arXiv API (title, abstract, authors); everything else
is fetched and stripped to headings and paragraphs. The source text is stored on the draft as
grounding, so the same Editor gate that guards the ML stream also blocks any number the
writer invented. The URL is kept out of the posts and lands in the trailing link reply.

Images you pass with `--image` are copied into `assets/` and used as-is; otherwise the
Illustrator draws one. `xswarm ingest schedule` refuses any draft that is not `approved` —
that gate is not bypassable by flags.

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
