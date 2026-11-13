#!/usr/bin/env bash
# Deploy (or preview) the Bicep template for one environment.
#
#   scripts/ci/deploy_infra.sh <dev|staging|prod> <what-if|create> [container-image]
#
# The container image is just another template parameter, so application releases and
# infrastructure changes go through the same, reviewable path. When no image is given, the image
# currently running is kept, so an infrastructure-only deployment never changes the app version.
#
# Optional environment variables (set from GitHub variables, none of them secret):
#   AZURE_LOCATION            region for the deployment metadata and resources (default swedencentral)
#   AZURE_DEPLOYER_OBJECT_ID  object id of the pipeline identity (grants it registry/search/Foundry roles)
#   BUDGET_CONTACT_EMAILS     JSON array of budget alert recipients, e.g. '["me@example.org"]'
#   BUDGET_START_DATE         first day of a month (YYYY-MM-01); set once, a budget's start date is fixed
#   ENTRA_AUTH_CLIENT_ID      enables Container Apps authentication when set
set -euo pipefail

ENVIRONMENT="${1:?environment (dev|staging|prod) is required}"
MODE="${2:?mode (what-if|create) is required}"
IMAGE="${3:-}"
LOCATION="${AZURE_LOCATION:-swedencentral}"
RESOURCE_GROUP="rg-pdc-${ENVIRONMENT}"
CONTAINER_APP="ca-pdc-${ENVIRONMENT}"

if [[ -z "${IMAGE}" ]]; then
  IMAGE="$(az containerapp show --resource-group "${RESOURCE_GROUP}" --name "${CONTAINER_APP}" \
    --query 'properties.template.containers[0].image' --output tsv 2>/dev/null || true)"
fi

PARAMETERS=(--parameters "infra/parameters/${ENVIRONMENT}.bicepparam" --parameters "location=${LOCATION}")
[[ -n "${IMAGE}" ]] && PARAMETERS+=(--parameters "containerImage=${IMAGE}")
[[ -n "${AZURE_DEPLOYER_OBJECT_ID:-}" ]] && PARAMETERS+=(--parameters "deployerPrincipalId=${AZURE_DEPLOYER_OBJECT_ID}")
[[ -n "${BUDGET_CONTACT_EMAILS:-}" ]] && PARAMETERS+=(--parameters "budgetContactEmails=${BUDGET_CONTACT_EMAILS}")
[[ -n "${BUDGET_START_DATE:-}" ]] && PARAMETERS+=(--parameters "budgetStartDate=${BUDGET_START_DATE}")
[[ -n "${ENTRA_AUTH_CLIENT_ID:-}" ]] && PARAMETERS+=(--parameters "entraAuthClientId=${ENTRA_AUTH_CLIENT_ID}")

COMMON=(--name "pdc-${ENVIRONMENT}" --location "${LOCATION}" --template-file infra/main.bicep "${PARAMETERS[@]}")

case "${MODE}" in
  what-if)
    az deployment sub what-if "${COMMON[@]}"
    ;;
  create)
    echo "Deploying ${ENVIRONMENT} with image: ${IMAGE:-<template default placeholder>}" >&2
    az deployment sub create "${COMMON[@]}" --query properties.outputs --output json
    ;;
  *)
    echo "unknown mode: ${MODE}" >&2
    exit 2
    ;;
esac
