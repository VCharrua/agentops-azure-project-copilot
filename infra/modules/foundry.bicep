// Microsoft Foundry resource (kind AIServices) with one project and two model deployments.
// Key authentication is disabled: callers need an Entra ID identity with the Foundry User role.

param accountName string
param projectName string
param location string
param tags object
param chatModel object
param embeddingModel object

@description('Principal that calls models and agents at runtime (the app identity).')
param appPrincipalId string

@description('Principal that registers agent versions and runs live evals (the pipeline identity). Empty to skip.')
param deployerPrincipalId string

// "Foundry User" (formerly "Azure AI User"): data-plane access to models and agents.
var foundryUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Project Delivery Copilot'
    description: 'Synthetic-data reference implementation'
  }
}

resource chat 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: chatModel.name
  sku: {
    name: chatModel.skuName
    capacity: chatModel.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModel.name
      version: chatModel.version
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource embedding 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: embeddingModel.name
  // Deployments on one account must be created one at a time.
  dependsOn: [chat]
  sku: {
    name: embeddingModel.skuName
    capacity: embeddingModel.capacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel.name
      version: embeddingModel.version
    }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

resource appAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, appPrincipalId, foundryUserRoleId)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

resource deployerAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: account
  name: guid(account.id, deployerPrincipalId, foundryUserRoleId)
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleId)
  }
}

output projectEndpoint string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output chatDeploymentName string = chat.name
output embeddingDeploymentName string = embedding.name
