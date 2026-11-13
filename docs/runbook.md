# Runbook

Operational procedures for the Project Delivery Copilot. The Azure procedures are written from the
design and **have not yet been exercised on a live environment**; treat the first use of each as a
rehearsal and correct this document afterwards.

Names used below: resource group `rg-pdc-<env>`, container app `ca-pdc-<env>`, subscription
deployment `pdc-<env>`.

## Quick reference

| I need to... | Do this |
| --- | --- |
| Check health | `curl <url>/healthz` and `curl <url>/version` |
| Run the full health gate | `python scripts/smoke_test.py --base-url <url>` |
| See what is deployed | `az containerapp show -g rg-pdc-<env> -n ca-pdc-<env> --query properties.template.containers[0].image` |
| Roll back | [Rollback](#rollback) |
| Stop all spend in dev | `gh workflow run teardown.yml -f confirm=rg-pdc-dev` |
| Stop the agent taking traffic | [Kill switch](#kill-switch) |

## Deployment

Normal path: merge to `main`. `cd.yml` builds once and promotes dev, staging, prod. Production
waits for a required reviewer.

Before approving production, check: the staging smoke test passed; the live eval gate passed; the
change list contains nothing unexpected (prompt version, tools, infrastructure what-if).

Infrastructure-only change: `infra.yml` with `action=what-if`, read it, then `action=deploy`. The
running image is preserved.

## Rollback

**Automatic.** If a deployment or its smoke test fails, the workflow redeploys the previous image
and marks the job failed. The job summary says "Rolled back".

**Manual**, to a known-good digest:

```bash
az login
bash scripts/ci/deploy_infra.sh <env> create <registry>.azurecr.io/project-delivery-copilot@sha256:<digest>
python scripts/smoke_test.py --base-url <url>
```

Find earlier digests in the CD run summaries or with
`az acr manifest list-metadata --registry <registry> --name project-delivery-copilot`.

Rollback restores the image, and with it the prompt and tool definitions. It does not restore the
search index, the audit log, or an agent version registered in Foundry.

Afterwards: open an issue, and add a golden case or smoke check that would have caught the fault.

## Kill switch

To stop the agent quickly without deleting anything:

```bash
az containerapp ingress disable -g rg-pdc-<env> -n ca-pdc-<env>
```

Re-enable by redeploying the template (`infra.yml`, `action=deploy`). Pending approvals are lost
if the app restarts (state is in memory).

## Incidents

### Suspected prompt injection or unsafe output

1. Find the run: in Application Insights, search traces for `agent.run` with
   `copilot.blocked = true`, or guardrail spans with `copilot.retrieval.withheld > 0`.
2. Check side effects: `GET /audit` (confirm `chain_valid` is true) and look for
   `action_executed` records. Every one must have a human `actor`.
3. If a write action was executed that should not have been: this is a severity-one defect in the
   approval path. Use the kill switch, preserve logs, and reverse the record in the affected
   system.
4. If the source was a document: remove it from the index and from the source system. The user was
   shown a security notice if screening caught it.
5. Add the input as a golden safety case before fixing.

### A proposal was approved by mistake

The audit log shows who approved what and when. Reverse the record in the target system (task,
draft, incident entry). Emails are only ever drafts, so nothing was sent.

### Guardrail service unavailable

Symptom: every request is refused with `guardrail_unavailable`. This is the fail-closed default.
Check Azure AI Content Safety health and the app identity's role assignment. Setting
`COPILOT_GUARDRAIL_FAIL_CLOSED=false` keeps the service answering with only the local heuristics;
treat that as a conscious, temporary risk acceptance.

### Model errors or throttling (HTTP 429 from Foundry)

Check deployment quota and usage in the Foundry portal. Raise `capacity` in the `chatModel`
parameter or reduce traffic. The agent loop does not retry on its own beyond the SDK defaults.

### Answers stopped citing documents, or "could not find" for known content

Likely an empty or stale index. Re-run indexing:

```bash
COPILOT_SEARCH_ENDPOINT=<...> COPILOT_FOUNDRY_PROJECT_ENDPOINT=<...> python scripts/index_documents.py
```

Then run the live evals (`agent-evals.yml`, tier `live`).

### Eval gate fails on a pull request

Read the report comment on the PR. A failed safety metric is a blocker: do not lower the
threshold. For a quality metric, decide whether the change regressed behaviour or the expectation
is out of date; a threshold change needs a justification in the PR.

### Live evals fail in CD after dev deploy

Promotion stops; dev keeps the new image. Either fix forward or roll dev back manually. Nothing has
reached staging or production.

### Cost alert

1. Cost analysis on the resource group, grouped by meter.
2. Usual suspects: token usage (check `gen_ai.client.token.usage`), an always-on replica in dev,
   log ingestion.
3. For dev, tear down. For others, disable ingress if traffic is abusive, and turn on
   authentication if it is not already.

## Routine tasks

| Task | How |
| --- | --- |
| Dependency updates | Review and merge Dependabot PRs; CI and the eval gate run on them |
| Model version change | Change `chatModel` in the parameter file; what-if; deploy to dev; live evals must pass before promotion |
| Prompt change | Edit `agent/prompts.py`, bump `PROMPT_VERSION`, add golden cases, open a PR |
| New tool | Add the argument model and `ToolSpec`; classify read or write; add tests and golden cases |
| Rotate anything | Nothing to rotate: there are no stored credentials. Review role assignments instead |
| Audit export | `GET /audit` for the live chain; Log Analytics for history (`copilot.audit` logger) |

## Known operational constraints

- Single replica; pending approvals and conversation memory are lost on restart or scale-to-zero.
- A budget's start date cannot be moved once created; keep `BUDGET_START_DATE` stable.
- Purge soft-deleted Key Vault and Cognitive Services resources before recreating an environment
  with the same names.
