// Azure Container Registry. Admin user disabled: pulls and pushes use Entra ID identities only.

@minLength(5)
@maxLength(50)
param name string
param location string
param tags object

@description('Principal that pulls images (the app identity).')
param pullPrincipalId string

@description('Principal that pushes and imports images (the pipeline identity). Empty to skip.')
param deployerPrincipalId string

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrPushRoleId = '8311e382-0749-4cb8-b61a-304f252e45ec'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, pullPrincipalId, acrPullRoleId)
  properties: {
    principalId: pullPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
  }
}

resource push 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: registry
  name: guid(registry.id, deployerPrincipalId, acrPushRoleId)
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
