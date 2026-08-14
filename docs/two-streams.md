# Two streams, one machine

The repo now runs two content operations that share a spine and differ where they must.

```
                    shared core
  sources/  llm.py  models.py  db.py  costs.py  publisher.py  dashboard.py
        |                                              |
   ML stream (src/xswarm/agents)              Care stream (src/xswarm/care)
   Scout -> Curator -> Analyst                Scout -> Curator -> Evidence
     -> Writer -> Composer                      -> Angle -> Article writer
     -> Editor -> Visualizer                    -> Compliance editor -> Promoter
        |                                              |
   X posts and threads                        Blog articles + X/LinkedIn promo
        \______________________  dashboard  ______________________/
```

## Why two streams and not one pipeline with a flag

The two jobs disagree on almost everything that matters editorially:

| | ML stream | Care stream |
|---|---|---|
| Unit of output | a 270-char post | a 900-1,400 word article, plus promo posts |
| Reader | ML engineers | providers, clinicians who train, family caregivers |
| Voice | first-person operator, opinionated | institutional, plain, never prescriptive |
| Grounding bar | claim must appear in the paper | claim must carry a citable source, and regulatory claims must cite CMS or the Federal Register |
| Worst failure | a wrong benchmark number | telling a family to do something clinical, or promising a payer will reimburse |

Sharing prompts between those would make both worse. Sharing plumbing — HTTP,
feeds, model calls, cost accounting, scheduling, analytics — costs nothing and is
where all the actual code lives, so that is exactly what is shared.

## What the care stream knows about the business

Read from alvernahealth.com and stored as product facts the writer must not
contradict (`xswarm care sync-site`):

- Alverna delivers 1-on-1, clinician-led caregiver training by telehealth, tied to a
  specific patient's treatment plan, aligned to Medicare Caregiver Training Services.
- Three audiences, three different reasons to read:
  - **Providers / health systems** refer from the EHR; the value is capacity, safer
    discharges, and closed-loop documentation back into the chart.
  - **Clinicians (PT, OT, SLP, NP, CNS, psychologist, LCSW)** join the trainer network;
    the value is flexible telehealth work with scheduling, notes, and billing handled.
  - **Family caregivers** receive the training; the value is being taught the specific
    task they were sent home to perform.
- Training splits into functional (transfers, mobility, falls, ADLs/IADLs, swallowing,
  communication) and behavioral (dementia behaviors, agitation, routines, coping).

## Care pillars

1. **Policy explainer** — what a CMS rule actually changes, with the code and effective date.
2. **Reimbursement mechanics** — who may bill CTS, consent, documentation, telehealth status.
3. **Caregiver skills** — one task, explained the way a clinician would teach it.
4. **Discharge and transitions** — the readmission story, told operationally.
5. **Clinician career** — what telehealth caregiver training work is really like.
6. **Field signal** — what caregivers say on public forums, and what it implies for providers.

## Safety model

The compliance editor is deterministic and runs before any model-based review:

- every statistic and every regulatory statement must map to a source URL in the brief;
- reimbursement is never promised — "may be billable" survives, "you will be paid" does not;
- no diagnosis, dosing, or personalized clinical direction; no "cure", "guaranteed", "risk-free";
- no PHI, no invented patient stories;
- an audience-appropriate disclaimer is required on every article;
- CMS/statutory claims must cite cms.gov, medicare.gov, federalregister.gov or ecfr.gov —
  a blog post is not a source for a rule.

Reddit and LinkedIn are treated as **signal, not evidence**: they can motivate a piece
and be quoted as sentiment, never used to support a factual claim.

## Dashboard

`xswarm dash` writes one HTML page covering both streams: output volume and editor
pass rate, model cost, X engagement per pillar, article traffic, and a live crawl/SEO
check (robots.txt, sitemap, canonical/meta/OG tags, indexability) for the blog and for
alvernahealth.com. The point is comparability — the same table shape for both streams,
so it is obvious which kind of content actually spreads.
