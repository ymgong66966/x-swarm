# Workflow files that need a human to move them

My token cannot write under `.github/workflows/`, so changes to scheduled workflows land here
first. Copy each file over its counterpart in `.github/workflows/` after merging.

- `care-watch.yml`, `daily.yml`, `weekly-strategy.yml`: pass the per-stream Typefully social
  sets (`XSWARM_TYPEFULLY_CARE_SOCIAL_SET_ID`, `XSWARM_TYPEFULLY_ML_SOCIAL_SET_ID`) instead of
  the single `XSWARM_TYPEFULLY_SOCIAL_SET_ID`. Add both as repository secrets.
