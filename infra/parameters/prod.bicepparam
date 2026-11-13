using '../main.bicep'

// Prod: longer log retention (audit trail), purge protection on Key Vault (set in main.bicep).
// maxReplicas stays at 1 until approval state moves to a durable store (documented limitation).

param environmentName = 'prod'
param location = 'swedencentral'
param searchSku = 'basic'
param contentSafetySku = 'S0'
param minReplicas = 1
param maxReplicas = 1
param logRetentionDays = 90
param logDailyQuotaGb = 2
param monthlyBudgetAmount = 250
param tags = {
  costCenter: 'portfolio'
  disposable: 'false'
}
