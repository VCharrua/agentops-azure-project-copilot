# ADR 0001: Bicep for infrastructure as code

Status: accepted

## Context

The solution runs only on Azure. Infrastructure must be reviewable in pull requests, deployable
from GitHub Actions without stored credentials, and cheap to tear down and recreate.

## Decision

Use Bicep, with one module per concern and one `.bicepparam` file per environment.

## Reasons

- **No state file.** Azure Resource Manager is the source of truth. There is no remote state to
  host, lock, encrypt or recover, which removes a secret-bearing component from the pipeline.
- **Native what-if.** `az deployment sub what-if` produces the change preview that CI posts on
  pull requests, with no extra tooling.
- **Day-zero coverage.** New resource types and API versions (the Foundry project resource used
  here) are available as soon as ARM exposes them.
- **Type checking at build time.** `az bicep build` validates property names against the resource
  schema; the build runs in CI and caught mistakes while this template was written.

## Trade-offs

- Not portable. A multi-cloud or multi-provider estate (DNS, GitHub, Datadog in one plan) is
  Terraform territory.
- No plan artifact that is later applied byte-for-byte; what-if is advisory and can report noise.
- Deleting a resource from the template does not delete it in Azure (incremental mode). Teardown
  is therefore explicit: `teardown.yml` for dev. Deployment stacks would close this gap.

## When to revisit

If the project grows beyond Azure resources, or an organisation standard mandates Terraform.
