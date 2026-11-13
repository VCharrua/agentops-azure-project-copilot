# FinOps and cost notes

**None of this has been measured**, because nothing has been deployed. The figures below are list
prices read from the public [Azure Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)
on 2026-11-13 for Sweden Central, in EUR, pay-as-you-go. Prices change and differ by agreement:
re-check with the [pricing calculator](https://azure.microsoft.com/pricing/calculator/) before
relying on them.

## SKU choices

| Resource | SKU | Why | Billing model |
| --- | --- | --- | --- |
| Container Apps | Consumption profile, 0.5 vCPU / 1 GiB, dev scales to zero | Pay per use; no idle cost in dev | Per vCPU-second and GiB-second while active, per million requests, with a monthly free grant |
| Azure AI Search | Basic | Smallest tier with an SLA and enough for one small index | **Fixed hourly** |
| Container Registry | Basic | Enough storage for a handful of images | **Fixed daily** |
| Foundry model deployments | Global Standard, pay per token | No provisioned capacity to pay for when idle | Per token |
| Content Safety | S0 (F0 free tier is a parameter option) | Pay per request | Per 1,000 text records |
| Log Analytics | Pay-as-you-go, 30-day retention, 1 GB/day cap | The cap bounds the worst case | Per GB ingested |
| Key Vault | Standard | One secret, few operations | Per 10,000 operations |
| Managed identity, budget | n/a | n/a | No charge |

## Expected monthly cost of the dev environment

Two resources bill whether or not anything is used:

| Item | List price | Per month (730 h / 30.4 days) |
| --- | --- | --- |
| Azure AI Search Basic, 1 unit | 0.0907 EUR/hour | about 66 EUR |
| Container Registry Basic | 0.1496 EUR/day | about 4.50 EUR |
| **Fixed floor** | | **about 71 EUR** |

Everything else is usage-based and, for a demo environment that scales to zero and sees occasional
traffic, expected to be small next to the fixed floor: Container Apps active time, model tokens
(the offline eval tier uses none; the live tier sends the golden dataset, 51 cases, per run),
Content Safety requests, log ingestion (capped) and Key Vault operations. No number is given for
these because it would be a guess.

So: plan for **roughly 71 EUR/month fixed, plus usage**, while a dev environment exists. The dev
budget alert is set at 150 to leave headroom and still catch a runaway.

Ways to lower it:

- **Tear dev down when idle.** `teardown.yml` removes the whole resource group, including the two
  fixed-cost resources. Recreating it is one `infra.yml` run. This is the main lever.
- `searchSku = 'free'` removes most of the fixed floor (one free search service per subscription,
  small quotas, no SLA).
- `contentSafetySku = 'F0'` for the free tier.

Staging and prod add one always-on replica each (`minReplicas = 1`), which adds Container Apps
active-usage charges on top of the same fixed floor per environment.

## Cost controls in the repository

| Control | Where |
| --- | --- |
| Budget per resource group, alerts at 80% actual and 100% forecast | `infra/modules/budget.bicep` |
| Log Analytics daily ingestion cap and bounded retention | `infra/modules/monitoring.bicep` |
| Scale to zero in dev; replica cap | `infra/parameters/dev.bicepparam` |
| Step limit per agent run, message length limit | `config.py`, `api/app.py` |
| Token usage on every trace and as a metric | `observability/telemetry.py` |
| Free offline eval tier on PRs; paid live tier only nightly and in CD | `agent-evals.yml` |
| Manual teardown of dev with purge | `teardown.yml` |
| Tags on every resource (`environment`, `costCenter`, `workload`) for cost allocation | `infra/main.bicep` |

Limits of these controls: a budget **alerts, it does not stop spending**; the unauthenticated dev
endpoint can be made to consume tokens by anyone who finds it (see the threat model, T8); and the
nightly live eval run costs tokens every night it is enabled.

## Watching token cost once deployed

`gen_ai.client.token.usage` is emitted per model call with a token-type dimension, and each
`agent.run` span carries input and output token counts. In Application Insights that gives tokens
per request, per day and per tool path, which is the basis for a cost-per-conversation figure. No
such figure is quoted here because none has been observed.
