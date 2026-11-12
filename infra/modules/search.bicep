// Azure AI Search for hybrid (keyword + vector) retrieval. API keys are disabled: RBAC only.

param name string
param location string
param tags object

@allowed(['free', 'basic', 'standard'])
param sku string

@description('Principal that queries the index at runtime (the app identity).')
param readerPrincipalId string

@description('Principal that creates the index and uploads documents (the pipeline identity). Empty to skip.')
param deployerPrincipalId string

var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
    semanticSearch: 'disabled'
  }
}

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, readerPrincipalId, searchIndexDataReaderRoleId)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}

resource indexWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: search
  name: guid(search.id, deployerPrincipalId, searchIndexDataContributorRoleId)
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
  }
}

resource indexAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: search
  name: guid(search.id, deployerPrincipalId, searchServiceContributorRoleId)
  properties: {
    principalId: deployerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
  }
}

output endpoint string = 'https://${search.name}.search.windows.net'
