// Project Delivery Copilot - infrastructure entry point.
//
// Subscription-scoped: creates one resource group per environment and deploys every module into it.
// Design rules, enforced here rather than left to convention:
//   * no keys or connection strings in app settings: one user-assigned managed identity + RBAC;
//   * local (key) authentication disabled on Foundry, AI Search, Content Safety and the registry;
//   * the only secret-like value (App Insights connection string) lives in Key Vault;
//   * every environment has a cost budget with alerts.
//
// NOT YET VALIDATED ON AZURE: this template compiles and lints cleanly, but has not been deployed.

targetScope = 'subscription'

@description('Environment name. Drives resource names and sizing.')
@allowed(['dev', 'staging', 'prod'])
param environmentName string

@description('Azure region for all resources. Check model availability for the chosen region.')
param location string = 'swedencentral'

@description('Short prefix for resource names.')
@minLength(2)
@maxLength(6)
param namePrefix string = 'pdc'

@description('Container image to run. The default placeholder lets the first deployment succeed before any image has been pushed; the CD pipeline then sets the real image.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Chat model deployed to Foundry. Verify the model and version are available and not scheduled for retirement in your region.')
param chatModel object = {
  name: 'gpt-4.1-mini'
  version: '2025-04-14'
  skuName: 'GlobalStandard'
  capacity: 30
}

@description('Embedding model deployed to Foundry.')
param embeddingModel object = {
  name: 'text-embedding-3-small'
  version: '1'
  skuName: 'GlobalStandard'
  capacity: 30
}

@description('Azure AI Search SKU. basic is the smallest tier with an SLA; free is possible for experiments (one per subscription).')
@allowed(['free', 'basic', 'standard'])
param searchSku string = 'basic'

@description('Content Safety SKU. F0 is the free tier (one per subscription and region).')
@allowed(['F0', 'S0'])
param contentSafetySku string = 'S0'

@description('Minimum and maximum replicas. Keep max at 1 until approval state moves to a durable store (see docs/architecture.md, limitations).')
param minReplicas int = 0
param maxReplicas int = 1

@description('Log Analytics retention in days and daily ingestion cap in GB (cost guardrail).')
param logRetentionDays int = 30
param logDailyQuotaGb int = 1

@description('Monthly cost budget for the resource group, in the billing currency.')
param monthlyBudgetAmount int = 150

@description('Email addresses that receive budget alerts. Supplied at deployment time, never committed.')
param budgetContactEmails array = []

@description('First day of the month the budget starts. Must not be in the past month when first created.')
param budgetStartDate string = utcNow('yyyy-MM-01')

@description('Object id of the identity that runs the pipelines (GitHub OIDC service principal). Grants it push to the registry and index/agent management. Empty skips these role assignments.')
param deployerPrincipalId string = ''

@description('Entra ID application (client) id used for Container Apps built-in authentication. Empty leaves authentication off, which is only acceptable for short-lived dev environments with synthetic data.')
param entraAuthClientId string = ''

@description('Tags applied to every resource.')
param tags object = {}

var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var suffix = take(uniqueString(subscription().id, namePrefix, environmentName), 6)
var baseName = '${namePrefix}-${environmentName}'
var allTags = union(
  {
    workload: 'project-delivery-copilot'
    environment: environmentName
    managedBy: 'bicep'
    dataClassification: 'synthetic'
  },
  tags
)

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${baseName}'
  location: location
  tags: allTags
}

module identity 'modules/identity.bicep' = {
  scope: rg
  name: 'identity'
  params: {
    name: 'id-${baseName}'
    location: location
    tags: allTags
  }
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    baseName: baseName
    location: location
    tags: allTags
    retentionDays: logRetentionDays
    dailyQuotaGb: logDailyQuotaGb
    appPrincipalId: identity.outputs.principalId
  }
}

module registry 'modules/registry.bicep' = {
  scope: rg
  name: 'registry'
  params: {
    name: '${namePrefix}${environmentName}acr${suffix}'
    location: location
    tags: allTags
    pullPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module keyVault 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    name: '${baseName}-kv-${suffix}'
    location: location
    tags: allTags
    enablePurgeProtection: environmentName == 'prod'
    readerPrincipalId: identity.outputs.principalId
    appInsightsName: monitoring.outputs.appInsightsName
  }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  name: 'foundry'
  params: {
    accountName: '${baseName}-foundry-${suffix}'
    projectName: 'project-delivery-copilot'
    location: location
    tags: allTags
    chatModel: chatModel
    embeddingModel: embeddingModel
    appPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module search 'modules/search.bicep' = {
  scope: rg
  name: 'search'
  params: {
    name: '${baseName}-search-${suffix}'
    location: location
    tags: allTags
    sku: searchSku
    readerPrincipalId: identity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

module contentSafety 'modules/contentsafety.bicep' = {
  scope: rg
  name: 'contentsafety'
  params: {
    name: '${baseName}-safety-${suffix}'
    location: location
    tags: allTags
    sku: contentSafetySku
    userPrincipalId: identity.outputs.principalId
  }
}

module app 'modules/containerapp.bicep' = {
  scope: rg
  name: 'containerapp'
  params: {
    baseName: baseName
    location: location
    tags: allTags
    containerImage: containerImage
    isPlaceholderImage: containerImage == placeholderImage
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    registryLoginServer: registry.outputs.loginServer
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    appInsightsSecretUri: keyVault.outputs.appInsightsSecretUri
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    entraAuthClientId: entraAuthClientId
    environmentVariables: {
      COPILOT_MODE: 'azure'
      COPILOT_FOUNDRY_PROJECT_ENDPOINT: foundry.outputs.projectEndpoint
      COPILOT_FOUNDRY_MODEL_DEPLOYMENT: foundry.outputs.chatDeploymentName
      COPILOT_FOUNDRY_EMBEDDING_DEPLOYMENT: foundry.outputs.embeddingDeploymentName
      COPILOT_SEARCH_ENDPOINT: search.outputs.endpoint
      COPILOT_SEARCH_INDEX: 'project-docs'
      COPILOT_CONTENT_SAFETY_ENDPOINT: contentSafety.outputs.endpoint
      COPILOT_APPROVER_HEADER: empty(entraAuthClientId) ? 'X-Approver' : 'X-MS-CLIENT-PRINCIPAL-NAME'
    }
  }
}

module budget 'modules/budget.bicep' = {
  scope: rg
  name: 'budget'
  params: {
    name: 'budget-${baseName}'
    amount: monthlyBudgetAmount
    startDate: budgetStartDate
    contactEmails: budgetContactEmails
  }
}

output resourceGroupName string = rg.name
output containerAppName string = app.outputs.name
output containerAppUrl string = app.outputs.url
output registryName string = registry.outputs.name
output registryLoginServer string = registry.outputs.loginServer
output keyVaultName string = keyVault.outputs.name
output managedIdentityClientId string = identity.outputs.clientId
output foundryProjectEndpoint string = foundry.outputs.projectEndpoint
output chatDeploymentName string = foundry.outputs.chatDeploymentName
output embeddingDeploymentName string = foundry.outputs.embeddingDeploymentName
output searchEndpoint string = search.outputs.endpoint
output contentSafetyEndpoint string = contentSafety.outputs.endpoint
