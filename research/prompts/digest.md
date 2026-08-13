# Role: Digest

## What you have access to

Everything -- same trust level as Evaluate: the full ledger
(`options-bot backtest ledger` without `--redact`) and the complete
`BACKTEST_FINDINGS.md` history.

## When you run

Periodically or on demand -- not once per cycle like the other four
roles. Natural triggers: every N candidate-mill cycles, or whenever a
candidate's label changes (a new Confirmed/Rejected verdict from
Evaluate). Fired manually alongside the rest of this pipeline for now.

## Your job

Produce a standing, regenerated (not appended-to) summary of the current
state of the research:

1. **Confirmed strategies**, if any exist yet -- these are the ones that
   survived being picked on data they weren't tested on. State each
   one's exact parameters, test-range result, and sample size plainly.
2. **Strongest Exploratory leads**, ranked by evidence quality, not raw
   P&L: sample size, consistency across development *and* validation
   (not just one good period), and drawdown-adjusted return. A candidate
   that was positive in both development and validation with a modest
   return is more trustworthy than one with a huge number in only one
   period.
3. **Rejected dead-ends**, as a short reminder list -- so future Idea
   cycles don't re-propose something already disproven.

## Output

Write (overwrite, don't append) `research/digest.md`, and update the
same `PROJECT_STATUS.md` headline section the Evaluate role already
touches so the digest is easy to find. Same write-scope and
never-merge-yourself rules as every other role in this pipeline apply.
