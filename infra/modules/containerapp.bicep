// Container Apps environment + the copilot API.
//   * Logs go to Log Analytics through diagnostic settings (no workspace shared key).
//   * Images are pulled with the managed identity (no registry credentials).
//   * The App Insights connection string is a Key Vault secret reference.
//   * Single-revision mode: a new revision only receives traffic after its readiness probe passes.

param baseName string
param location string
param tags object
param containerImage string

@description('True while the public placeholder image is deployed (it listens on port 80 and has no /healthz).')
param isPlaceholderImage bool

param identityId string
param identityClientId string
param registryLoginServer string
param logAnalyticsWorkspaceId string
param appInsightsSecretUri string
param minReplicas int
param maxReplicas int
param environmentVariables object

@description('Entra ID app (client) id for built-in authentication. Empty disables it.')
param entraAuthClientId string

var port = isPlaceholderImage ? 80 : 8000
var settingsEnv = [
  for item in items(environmentVariables): {
    name: item.key
    value: item.value
  }
]
var probes = [
  {
    type: 'Startup'
    httpGet: {
      path: '/healthz'
      port: port
    }
    periodSeconds: 5
    failureThreshold: 24
  }
  {
    type: 'Readiness'
    httpGet: {
      path: '/healthz'
      port: port
    }
    periodSeconds: 10
    failureThreshold: 3
  }
  {
    type: 'Liveness'
    httpGet: {
      path: '/healthz'
      port: port
    }
    periodSeconds: 30
    failureThreshold: 3
  }
]

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${baseName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource environmentLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${baseName}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: port
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          identity: identityId
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection-string'
          keyVaultUrl: appInsightsSecretUri
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(settingsEnv, [
            {
              // Tells DefaultAzureCredential which user-assigned identity to use.
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
          ])
          probes: isPlaceholderImage ? [] : probes
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

// Built-in authentication (Easy Auth) with Entra ID. With it enabled, unauthenticated requests are
// rejected at the platform edge and the caller identity arrives in X-MS-CLIENT-PRINCIPAL-NAME,
// which the API uses as the approver identity. Health probes are excluded.
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (!empty(entraAuthClientId)) {
  parent: app
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'Return401'
      excludedPaths: ['/healthz', '/version']
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraAuthClientId
          openIdIssuer: '${az.environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
        validation: {
          allowedAudiences: ['api://${entraAuthClientId}']
        }
      }
    }
  }
}

output name string = app.name
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
