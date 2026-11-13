# ADR 0004: GitHub Actions authenticates to Azure with OIDC federation

Status: accepted

## Context

Pipelines need to deploy infrastructure, push images and run live evaluations. The usual shortcut,
a service principal secret stored as a GitHub secret, is a long-lived credential that can leak
through logs, forks or a compromised action, and that someone has to rotate.

## Decision

Use workload identity federation. Each GitHub Environment (`dev`, `staging`, `prod`) has its own
Entra ID application with a federated credential whose subject is
`repo:<owner>/<repo>:environment:<name>`. A fourth, read-only identity is federated to
`pull_request` runs for Bicep what-if. Workflows request `id-token: write` only in the jobs that
log in.

Identifiers (client id, tenant id, subscription id) are stored as GitHub *variables*. They are not
secrets, and keeping them out of the secret store makes it obvious that the repository holds no
credentials at all.

## Reasons

- **Nothing to steal or rotate.** Tokens are issued per job and expire in minutes.
- **Trust is bound to context.** The prod identity can only be assumed by a job running in the
  `prod` environment, which has required reviewers. A pull request cannot obtain it.
- **Blast radius per environment.** A mistake in a dev workflow cannot touch prod.
- **Forks stay safe.** Fork pull requests get no token; the what-if job is skipped for them.

## Trade-offs

- One-time setup, automated by `scripts/ci/bootstrap_oidc.sh`.
- The deploy identities currently hold Contributor and RBAC Administrator on the subscription
  because the template creates resource groups and role assignments. Scoping them to pre-created
  resource groups is on the roadmap.
- The what-if identity needs a small custom role, since Reader alone cannot run what-if.

## Related controls

- Third-party actions are pinned to commit SHAs and updated by Dependabot.
- Workflow-level `permissions` default to `contents: read`.
- Untrusted values (PR titles) reach shell steps through environment variables, never by
  interpolation into the script.

## Validation status

The workflows pass actionlint. The federation setup has not yet been run against a tenant.
