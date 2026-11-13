// Key Vault (RBAC authorization). Holds the Application Insights connection string, which the
// Container App reads through a Key Vault secret reference with its managed identity.

@minLength(3)
@maxLength(24)
param name string
param location string
param tags object
param enablePurgeProtection bool

@description('Principal that reads secrets (the app identity).')
param readerPrincipalId string

param appInsightsName string

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Purge protection cannot be turned off once on, so it is reserved for prod; dev stays disposable.
    enablePurgeProtection: enablePurgeProtection ? true : null
  }
}

resource connectionString 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: vault
  name: 'appinsights-connection-string'
  properties: {
    value: appInsights.properties.ConnectionString
    contentType: 'text/plain'
  }
}

resource reader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, readerPrincipalId, keyVaultSecretsUserRoleId)
  properties: {
    principalId: readerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

output name string = vault.name
output appInsightsSecretUri string = connectionString.properties.secretUri
