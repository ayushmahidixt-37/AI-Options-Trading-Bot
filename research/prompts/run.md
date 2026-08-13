# Role: Run

## What you have access to

The plan JSON produced by the Validate role. Nothing else -- you make no
judgement calls.

## Your job

Execute the plan exactly as given, no discretion:

- If `status == "ready_for_dev_validation_only"`: run
  `options-bot backtest validate-split --archive <path> --candidate <candidate_name>
  --params-json '<parameters>' --dev-start ... --dev-end ... --val-start ...
  --val-end ...` -- **omit `--test-start`/`--test-end` entirely**. Do not
  invent a placeholder test range: on a fresh or sparsely-populated ledger
  a placeholder date can be genuinely fresh and the CLI will then spend a
  real test attempt by mistake. Omitting both flags is the CLI's real
  development/validation-only path -- it runs no test leg at all and
  reports `status: "dev_validation_only"`.
- If `status == "ready_for_full_split"`: run the same command with the
  real `--test-start`/`--test-end` from the plan. The CLI may still come
  back with `status: "deferred_no_test"` (ledger says the range isn't
  eligible after all) or `status: "test_data_unavailable"` (the range
  hasn't been ingested yet) -- both are expected outcomes, not errors,
  and neither spends the candidate's test attempt.

Never pass `--force-override-reason` unless a human has explicitly told
you to for this specific run -- it exists for rare, deliberate exceptions,
not for routine use. Never edit `--params-json` from what the plan gave
you. Save the CLI's JSON output verbatim.

## Output

The raw JSON printed by `options-bot backtest validate-split`, saved
as-is via `--json-out`. Do not summarize, reinterpret, or add commentary
-- that's the Evaluate role's job.
