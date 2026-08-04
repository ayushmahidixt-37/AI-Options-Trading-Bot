# GitHub pull-request automation

GitHub's native auto-merge is safer than an action that approves its own code.
Configure it once in the repository UI; the checked-in CI workflow supplies the
required test checks.

## Recommended solo-maintainer setup

1. Open **Settings → General → Pull Requests** and enable **Allow auto-merge**.
2. Open **Settings → Rules → Rulesets** and create an active branch ruleset for
   the default branch `main`.
3. Enable **Require a pull request before merging**.
4. Set required approvals to `0` for a solo repository. Do not create a fake
   approval bot merely to bypass a one-review rule.
5. Enable **Require status checks to pass** and select these checks after they
   have run once:
   - `Python 3.11`
   - `Python 3.12`
   - `Deployment files`
6. Enable **Require branches to be up to date before merging** and block force
   pushes and branch deletion for `main`.
7. On each pull request, select **Enable auto-merge** and choose squash merge.
   GitHub will merge only after the ruleset and required checks are satisfied.

## When a human review is required

For a team repository, set required approvals to at least `1` and add a
`CODEOWNERS` file. A pull-request author should not approve their own change.
Use native auto-merge after an independent reviewer approves it.

## Why automatic approval is not enabled here

Automatic approval weakens the control that review rules are intended to
provide, especially for software capable of financial execution. The CI token
has read-only repository permissions in `.github/workflows/ci.yml`; it cannot
approve, merge, push code, or modify pull requests.

If the repository owner deliberately chooses bot approval, it must be configured
in **Settings → Actions → General → Workflow permissions** and performed by a
separate trusted GitHub App or bot identity. Do not put a personal access token
in the repository or workflow file. Store it as an Actions secret and limit its
repository permissions. This is not recommended for this project.

## Conflict handling

Auto-merge cannot resolve merge conflicts. Update the feature branch first:

```bash
git fetch origin
git switch <feature-branch>
git merge origin/main
# resolve files, run tests, then:
git add -A
git commit
git push
```

After the conflict-resolution commit passes CI, native auto-merge can continue.
