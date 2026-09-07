# Workflow files that need a human to move them

My token cannot write under `.github/workflows/`, so changes to scheduled workflows land here
first. Copy each file over its counterpart in `.github/workflows/` after merging.

Waiting: `review-action.yml`, the workflow the hosted review UI dispatches when you approve or
revise a draft. Until it sits at `.github/workflows/review-action.yml` on the default branch,
those buttons fail with a 404 from GitHub.

```bash
git mv ci/workflows/review-action.yml .github/workflows/review-action.yml
git commit -m "Add the review-action workflow"
```
