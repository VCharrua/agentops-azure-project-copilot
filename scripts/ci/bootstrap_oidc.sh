#!/usr/bin/env bash
# One-time setup: connect this GitHub repository to an Azure subscription with OIDC federation.
#
#   az login
#   gh auth login
#   scripts/ci/bootstrap_oidc.sh <github-owner>/<github-repo> [location]
#
# What it creates (no client secrets anywhere):
#   * one Entra ID app registration + service principal per environment (dev, staging, prod), each
#     trusted ONLY for workflow runs in the matching GitHub Environment;
#   * one read-only "plan" identity trusted ONLY for pull_request runs (Bicep what-if comments);
#   * role assignments on the subscription;
#   * GitHub Environments and the repository/environment *variables* the workflows read.
#
# Review before running. NOT YET VALIDATED: written against the az and gh CLIs, never executed.
#
# Scope note: the Bicep template is subscription-scoped (it creates the resource groups and role
# assignments), so the deploy identities get Contributor + Role Based Access Control Administrator
# on the subscription. For a shared subscription, pre-create the resource groups and scope both
# roles to them instead.
set -euo pipefail

REPO="${1:?usage: bootstrap_oidc.sh <owner>/<repo> [location]}"
LOCATION="${2:-swedencentral}"
SUBSCRIPTION_ID="$(az account show --query id --output tsv)"
TENANT_ID="$(az account show --query tenantId --output tsv)"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"
ISSUER="https://token.actions.githubusercontent.com"

create_identity() { # <display-name> <federated-subject>  -> prints "<clientId> <objectId>"
  local name="$1" subject="$2" client_id object_id
  client_id="$(az ad app list --display-name "$name" --query '[0].appId' --output tsv)"
  if [[ -z "$client_id" ]]; then
    client_id="$(az ad app create --display-name "$name" --query appId --output tsv)"
    az ad sp create --id "$client_id" --output none
  fi
  object_id="$(az ad sp show --id "$client_id" --query id --output tsv)"
  if [[ -z "$(az ad app federated-credential list --id "$client_id" --query "[?subject=='${subject}'].name" --output tsv)" ]]; then
    az ad app federated-credential create --id "$client_id" --output none --parameters "{
      \"name\": \"github-${name}\",
      \"issuer\": \"${ISSUER}\",
      \"subject\": \"${subject}\",
      \"audiences\": [\"api://AzureADTokenExchange\"]
    }"
  fi
  echo "$client_id $object_id"
}

assign() { # <object-id> <role>
  az role assignment create --assignee-object-id "$1" --assignee-principal-type ServicePrincipal \
    --role "$2" --scope "$SCOPE" --output none
}

echo "Subscription ${SUBSCRIPTION_ID}, tenant ${TENANT_ID}, repository ${REPO}"

gh variable set AZURE_TENANT_ID --repo "$REPO" --body "$TENANT_ID"
gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUBSCRIPTION_ID"
gh variable set AZURE_LOCATION --repo "$REPO" --body "$LOCATION"
gh variable set BUDGET_START_DATE --repo "$REPO" --body "$(date -u +%Y-%m-01)"

for environment in dev staging prod; do
  read -r client_id object_id < <(create_identity "pdc-github-${environment}" "repo:${REPO}:environment:${environment}")
  assign "$object_id" "Contributor"
  assign "$object_id" "Role Based Access Control Administrator"
  gh api --method PUT "repos/${REPO}/environments/${environment}" --silent
  gh variable set AZURE_CLIENT_ID --repo "$REPO" --env "$environment" --body "$client_id"
  gh variable set AZURE_DEPLOYER_OBJECT_ID --repo "$REPO" --env "$environment" --body "$object_id"
  echo "  ${environment}: client id ${client_id}"
done

# Read-only identity for pull-request what-if. Reader alone cannot run what-if, so a small custom
# role adds the deployment operations (which cannot create or change any resource by themselves).
ROLE_NAME="Deployment What-If (${SUBSCRIPTION_ID})"
if [[ -z "$(az role definition list --name "$ROLE_NAME" --query '[0].name' --output tsv)" ]]; then
  az role definition create --output none --role-definition "{
    \"Name\": \"${ROLE_NAME}\",
    \"Description\": \"Run ARM/Bicep what-if. Cannot modify resources.\",
    \"Actions\": [\"*/read\", \"Microsoft.Resources/deployments/*\"],
    \"AssignableScopes\": [\"${SCOPE}\"]
  }"
fi
read -r plan_client_id plan_object_id < <(create_identity "pdc-github-plan" "repo:${REPO}:pull_request")
assign "$plan_object_id" "$ROLE_NAME"
gh variable set AZURE_PLAN_CLIENT_ID --repo "$REPO" --body "$plan_client_id"

cat <<EOF

Done. Still to do by hand (see docs/azure-setup.md):
  1. GitHub > Settings > Environments > prod: add required reviewers (and optionally a wait timer).
  2. Optional: gh variable set BUDGET_CONTACT_EMAILS --repo ${REPO} --body '["you@example.org"]'
  3. Run the "Infrastructure" workflow for dev (action: deploy), then staging and prod.
  4. Enable the pipelines: gh variable set AZURE_DEPLOY_ENABLED --repo ${REPO} --body true
EOF
