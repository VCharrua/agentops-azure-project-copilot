// Azure AI Content Safety (harm categories + Prompt Shields). Key authentication disabled.
// A custom subdomain is required for Entra ID (token) authentication.

param name string
param location string
param tags object

@allowed(['F0', 'S0'])
param sku string

@description('Principal that calls the service (the app identity).')
param userPrincipalId string

var cognitiveServicesUserRoleId = 'a97b65f3-24c7-4388-baec-2e87135dc908'

resource contentSafety 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'ContentSafety'
  sku: {
    name: sku
  }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource user 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: contentSafety
  name: guid(contentSafety.id, userPrincipalId, cognitiveServicesUserRoleId)
  properties: {
    principalId: userPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesUserRoleId)
  }
}

output endpoint string = contentSafety.properties.endpoint
