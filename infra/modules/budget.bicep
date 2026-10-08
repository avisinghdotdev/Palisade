// Monthly cost budget for the whole subscription: alerts on the forecast before money is spent,
// and once more when the actual spend crosses the amount.
targetScope = 'subscription'

param name string

@description('Monthly amount in the billing currency.')
param amount int

@description('First day of a month, ISO date. Fixed in the parameter file so redeploys never change it.')
param startDate string

param contactEmails string[]

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: { startDate: startDate }
    notifications: {
      forecast50: notification(50, 'Forecasted', contactEmails)
      forecast80: notification(80, 'Forecasted', contactEmails)
      forecast100: notification(100, 'Forecasted', contactEmails)
      actual100: notification(100, 'Actual', contactEmails)
    }
  }
}

func notification(threshold int, thresholdType string, emails string[]) object => {
  enabled: true
  operator: 'GreaterThan'
  threshold: threshold
  thresholdType: thresholdType
  contactEmails: emails
}
