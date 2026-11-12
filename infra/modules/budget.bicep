// Monthly cost budget on the resource group with two alerts:
//   80% of budget on actual spend, 100% of budget on forecast spend.
// Budgets alert; they do not stop resources. The teardown workflow is the hard stop for dev.

param name string
param amount int
param startDate string
param contactEmails array

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
        contactRoles: ['Owner', 'Contributor']
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: contactEmails
        contactRoles: ['Owner', 'Contributor']
      }
    }
  }
}
