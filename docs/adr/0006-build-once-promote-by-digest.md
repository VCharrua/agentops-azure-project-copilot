# ADR 0006: Build once, promote by digest, deploy through the template

Status: accepted

## Context

The release path must guarantee that what was tested is what reaches production, must fail safe,
and must leave environments independent of each other.

## Decision

- **Build once.** `cd.yml` builds one image per commit, pushes it to the dev registry and records
  its digest. No later job rebuilds.
- **One registry per environment, promotion by import.** `az acr import` copies the image by
  digest into the staging and prod registries; the workflow verifies the digest is unchanged.
  Deployments reference `repository@sha256:...`, never a mutable tag.
- **The image is a template parameter.** A release is a Bicep deployment with a new
  `containerImage` value, run by the same script as infrastructure changes.
- **Gates between stages.** dev deploy and smoke test, then live evals, then staging deploy and
  smoke test, then required reviewers, then prod deploy and smoke test.
- **Rollback.** The previous image is read before deploying. If the deployment or the smoke test
  fails, the workflow redeploys the previous image and the job fails.

## Reasons

- Rebuilding per environment can produce different bytes from the same commit (base image moved,
  dependency released). Promotion by digest rules that out.
- Per-environment registries mean prod does not pull from a registry that dev pipelines write to,
  and dev can be torn down without affecting prod.
- One deployment path means no drift between "what the app pipeline did" and "what the template
  says", and rollback is the forward operation with an older parameter.
- Container Apps single-revision mode already refuses to shift traffic to a revision that fails
  its readiness probe. The smoke test adds checks a probe cannot make: the expected version is
  serving, a grounded answer comes back, an injection probe is refused, and a write request
  executes nothing.

## Trade-offs

- A template deployment takes minutes. `az containerapp update` would be faster but creates a
  second, unreviewed path to change the app.
- Rollback restores the image, not data or a Foundry-registered agent version.
- There is no canary. Traffic splitting across revisions is on the roadmap.
- The pipeline identity needs rights to deploy the whole template on every release.

## Validation status

Written and linted; not yet executed against Azure.
