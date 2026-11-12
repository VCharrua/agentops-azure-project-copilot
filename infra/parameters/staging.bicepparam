using '../main.bicep'

// Staging: same shape as prod so promotion is meaningful; one warm replica.

param environmentName = 'staging'
param location = 'swedencentral'
param searchSku = 'basic'
param contentSafetySku = 'S0'
param minReplicas = 1
param maxReplicas = 1
param logRetentionDays = 30
param logDailyQuotaGb = 1
param monthlyBudgetAmount = 200
param tags = {
  costCenter: 'portfolio'
  disposable: 'false'
}
