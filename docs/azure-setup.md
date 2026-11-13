# Azure setup and first deployment

> **Not yet validated on Azure.** These steps describe how the pipeline is designed to be
> connected. They have not been executed end to end. Expect to adjust details on the first run,
> and please record what you change.

## Prerequisites

- An Azure subscription where you can create role assignments (Owner, or Contributor plus User
  Access Administrator) and Entra ID app registrations.
- Quota in the target region for the chat and embedding models in `infra/main.bicep`
  (defaults: `gpt-4.1-mini`, `text-embedding-3-small`, Global Standard). **Check current model
  availability and retirement dates for your region first** and override `chatModel` /
  `embeddingModel` if needed.
- A recent Azure CLI with Bicep (`az bicep install`), GitHub CLI, and the repository pushed to
  GitHub with Actions enabled. The deploy script combines a `.bicepparam` file with inline
  parameter overrides, which older CLI versions reject; developed against CLI 2.79 and Bicep 0.49.

No secret is created or stored at any point.

## Step 1. Connect the repository to Azure (OIDC)

```bash
az login
az account set --subscription "<subscription name or id>"
gh auth login
scripts/ci/bootstrap_oidc.sh <owner>/<repo> swedencentral
```

Read the script before running it. It creates one Entra ID application per environment, federated
to that GitHub Environment only, plus a read-only identity for pull-request what-if; assigns roles
on the subscription; creates the `dev`, `staging` and `prod` GitHub Environments; and sets these
**variables** (not secrets):

| Variable | Level | Used for |
| --- | --- | --- |
| `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION` | repository | Login and region |
| `BUDGET_START_DATE` | repository | Budget start (first of the current month; fixed after creation) |
| `AZURE_PLAN_CLIENT_ID` | repository | Read-only identity for what-if on pull requests |
| `AZURE_CLIENT_ID`, `AZURE_DEPLOYER_OBJECT_ID` | per environment | Deploy identity and its role assignments |
| `AZURE_DEPLOY_ENABLED` | repository | Master switch for the Azure workflows (you set it in step 4) |

Optional variables you can add:

```bash
gh variable set BUDGET_CONTACT_EMAILS --body '["you@example.org"]'
gh variable set ENTRA_AUTH_CLIENT_ID --env prod --body "<app registration client id>"   # see step 6
```

## Step 2. Protect production

In GitHub: **Settings > Environments > prod > Required reviewers**. Add at least one reviewer.
Optionally restrict deployment branches to `main` for all three environments. Then apply the
branch protection in [CONTRIBUTING.md](../CONTRIBUTING.md#branch-protection).

## Step 3. Create the dev infrastructure

```bash
gh variable set AZURE_DEPLOY_ENABLED --body true
gh workflow run infra.yml -f environment=dev -f action=what-if     # read the preview first
gh workflow run infra.yml -f environment=dev -f action=deploy
```

This creates `rg-pdc-dev` with a placeholder container image, because no image has been built yet.
Or from your machine: `make azure-whatif ENV=dev` and `make azure-deploy ENV=dev`.

Things that commonly need attention on a first deployment:

- **Role assignment propagation.** The app reads its Key Vault secret reference with a role
  assigned in the same deployment. If the Container App fails to provision with an access error,
  re-run the workflow.
- **Model availability or quota** errors on the Foundry deployments: change the model, version,
  SKU or capacity parameters.
- **Globally unique names** (registry, Key Vault, Foundry, search) are derived from the
  subscription id; a soft-deleted resource with the same name blocks re-creation until purged
  (`teardown.yml` purges for dev).

## Step 4. First release

```bash
gh workflow run cd.yml       # or merge a change to main
```

The pipeline builds the image once, deploys it to dev with the real port and probes, indexes the
synthetic documents into Azure AI Search, and runs the smoke test. If that passes, the live
evaluation runs against dev.

## Step 5. Staging and production

Create their infrastructure the same way, then let CD promote:

```bash
gh workflow run infra.yml -f environment=staging -f action=deploy
gh workflow run infra.yml -f environment=prod -f action=deploy
```

On the next CD run the same digest is imported into the staging registry and deployed; the prod
job then waits for the required reviewer.

## Step 6. Turn on authentication (required before any non-synthetic use)

Without it the API is public and the approver header is caller-supplied.

1. Create an Entra ID app registration for the API and expose an application ID URI
   `api://<client id>`.
2. Set `ENTRA_AUTH_CLIENT_ID` for the environment and redeploy. The template then enables
   Container Apps authentication, returns 401 to unauthenticated calls, and switches the approver
   header to the platform-provided `X-MS-CLIENT-PRINCIPAL-NAME`.
3. Grant the pipeline identity access to request tokens for that API so the smoke test and live
   evals can call it.

## Verify by hand

```bash
URL="$(az deployment sub show --name pdc-dev --query properties.outputs.containerAppUrl.value -o tsv)"
python scripts/smoke_test.py --base-url "$URL"
curl -s "$URL/version"
```

## Clean up

```bash
gh workflow run teardown.yml -f confirm=rg-pdc-dev
```

Deletes and purges dev. Staging and prod are removed deliberately by hand
(`az group delete --name rg-pdc-<env>`), followed by purging the soft-deleted Key Vault and
Cognitive Services accounts.
