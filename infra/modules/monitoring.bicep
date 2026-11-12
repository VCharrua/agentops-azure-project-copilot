// Log Analytics workspace + workspace-based Application Insights.
// Cost guardrails: bounded retention and a daily ingestion cap.

param baseName string
param location string
param tags object
param retentionDays int
param dailyQuotaGb int

@description('Principal allowed to publish telemetry with Entra ID authentication.')
param appPrincipalId string

var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${baseName}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionDays
    workspaceCapping: {
      dailyQuotaGb: dailyQuotaGb
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${baseName}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
  }
}

resource publisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, appPrincipalId, monitoringMetricsPublisherRoleId)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisherRoleId)
  }
}

output workspaceId string = workspace.id
output appInsightsName string = appInsights.name
