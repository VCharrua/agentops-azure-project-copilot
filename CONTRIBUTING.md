# Contributing

## Local workflow

```bash
pip install -e ".[dev,azure]"
make check        # lint + tests + offline evals: everything CI runs that needs no cloud
```

Use `make format` before committing. `make help` lists all targets.

## Conventional commits

Commit messages and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/).
CI checks the PR title; with squash merging, that title becomes the commit on `main`.

```text
<type>(<optional scope>): <description>
```

| Type | Use for |
| --- | --- |
| `feat` | New behaviour |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `test` | Tests only |
| `refactor` | No behaviour change |
| `perf` | Performance |
| `build` | Dependencies, Dockerfile, packaging |
| `ci` | Workflows and pipeline scripts |
| `chore` | Repository housekeeping |
| `revert` | Reverting a commit |

Scopes used so far: `agent`, `tools`, `retrieval`, `guardrails`, `approvals`, `evals`, `infra`,
`data`, `ops`, `cd`. Add `!` after the type or scope for a breaking change.

## Rules that reviewers enforce

- **Agent behaviour changes come with evaluation changes.** Editing the prompt, a tool, retrieval
  or a guardrail means adding or updating golden cases in the same PR and bumping `PROMPT_VERSION`
  when the prompt changed.
- **Write tools go through the approval gate.** A tool that changes anything is registered as
  `write` and has no handler in the registry.
- **Thresholds are not lowered to make a PR pass.** If a threshold must change, the PR explains
  why.
- **No secrets, tenant ids, subscription ids or real personal data**, in code, tests, docs or
  workflow files. Identifiers go in GitHub variables.
- **Synthetic data is generated.** Change `scripts/generate_synthetic_data.py`, then `make data`.
- **Third-party actions are pinned to a commit SHA** with the version in a comment.
- **Unverified is labelled.** Anything not run against Azure says "not yet validated on Azure".

## Branch protection

Recommended settings for `main` (GitHub > Settings > Rules > Rulesets, or Branches):

| Setting | Value |
| --- | --- |
| Require a pull request before merging | On, 1 approval, dismiss stale approvals, require review from Code Owners |
| Require status checks to pass | On, branch must be up to date |
| Required checks | `Lint and type-check`, `Tests and coverage`, `Secret and dependency scanning`, `CodeQL (python)`, `CodeQL (actions)`, `Container build, scan and smoke test`, `Workflow lint`, `Bicep build and lint`, `Offline evals (deterministic)`, `Conventional PR title` |
| Require conversation resolution | On |
| Require linear history | On (squash merge only) |
| Require signed commits | Recommended |
| Block force pushes and deletions | On |
| Do not allow bypassing the above | On, including administrators |

Note: `Offline evals (deterministic)` is path-filtered on pull requests. If you make it a required
check, either remove the `paths` filter in `agent-evals.yml` or use a ruleset that only requires it
when it runs; otherwise documentation-only PRs will wait for a check that never starts.

The same as a ruleset via the CLI (replace owner and repo):

```bash
gh api --method POST repos/<owner>/<repo>/rulesets --input - <<'JSON'
{
  "name": "main",
  "target": "branch",
  "enforcement": "active",
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": true,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "Lint and type-check" },
          { "context": "Tests and coverage" },
          { "context": "Secret and dependency scanning" },
          { "context": "CodeQL (python)" },
          { "context": "CodeQL (actions)" },
          { "context": "Container build, scan and smoke test" },
          { "context": "Workflow lint" },
          { "context": "Bicep build and lint" }
        ] } }
  ]
}
JSON
```

This ruleset payload has not been applied to a repository yet; check it against the current
GitHub REST reference before use.

Environment protection (Settings > Environments):

| Environment | Protection |
| --- | --- |
| `dev` | Deployment branches: `main` only |
| `staging` | Deployment branches: `main` only |
| `prod` | Required reviewers (at least one, ideally not the author), deployment branches: `main` only, optional wait timer |

Repository security settings worth enabling: secret scanning with push protection, Dependabot
alerts and security updates, private vulnerability reporting, and code scanning default alerts.
