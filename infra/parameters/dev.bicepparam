using '../main.bicep'

// Dev: cheapest sensible footprint, disposable (see teardown.yml). Scales to zero when idle.
// Identity ids, contact emails and the container image are supplied by the pipeline, never committed.

param environmentName = 'dev'
param location = 'swedencentral'
param searchSku = 'basic'
param contentSafetySku = 'S0'
param minReplicas = 0
param maxReplicas = 1
param logRetentionDays = 30
param logDailyQuotaGb = 1
param monthlyBudgetAmount = 150
param tags = {
  costCenter: 'portfolio'
  disposable: 'true'
}
