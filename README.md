# x-swarm

Agent swarm that reads the ML frontier every day, forms grounded takes, and drafts posts for
an X account. This repo currently implements **phases 1–2** of the plan: ingestion, curation,
analysis, drafting, and the editor gate. Visuals, publishing, and the learning loop come next.

```
Scout ──► Curator ──► Analyst ──► Writer ──► Editor ──► [review queue]
 │           │           │          │          │
 sources   scoring    grounded    3 variants  blocks anything
 (5)       + novelty  brief       per brief   ungrounded
```

Everything downstream of Scout is idempotent and stored in Postgres (SQLite by default for
local runs), so any stage can be rerun or replaced without redoing the others.

## Quickstart

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/xswarm run --dry-run      # full pipeline, no model spend, no credentials needed
.venv/bin/xswarm review             # drafts waiting on you
.venv/bin/xswarm approve 3          # or: approve 3 --reject --reason "too hypey"
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
| `xswarm run` | All of the above via the LangGraph pipeline |
| `xswarm review` / `approve` | Human-in-the-loop queue |
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
- `playbook.md` — learned posting rules. The Strategist agent will rewrite this weekly (phase 5),
  and each rewrite is a commit, so the file's history is the account's learning history.

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

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```
