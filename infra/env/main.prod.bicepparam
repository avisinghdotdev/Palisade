using 'main.bicep'

param workload = 'palisade'
param environment = 'prod'
param resourceGroupName = 'rg-palisade-prod'
param logAnalyticsName = 'log-palisade-prod'
param keyVaultName = 'kv-palisade-prod'
param containerAppsEnvironmentName = 'cae-palisade-prod'
param webAppName = 'ca-palisade-web-prod'
param apiAppName = 'ca-palisade-api-prod'
param webIdentityResourceId = '/subscriptions/100ad7a4-aeed-422e-87aa-e3adc5706c7d/resourceGroups/rg-palisade-prod/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-palisade-web-prod'
param apiIdentityResourceId = '/subscriptions/100ad7a4-aeed-422e-87aa-e3adc5706c7d/resourceGroups/rg-palisade-prod/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-palisade-api-prod'
param registryLoginServer = 'cravisingh.azurecr.io'
param imageTag = readEnvironmentVariable('IMAGE_TAG')
param revisionSuffix = readEnvironmentVariable('REVISION_SUFFIX')
param originCertificateName = 'cert-palisade-origin-prod'
param customDomains = ['palisade.avisingh.dev']
// Cloudflare IPv4 ranges, https://www.cloudflare.com/ips-v4, as of 2026-10-09.
param allowedInboundCidrs = [
  '173.245.48.0/20'
  '103.21.244.0/22'
  '103.22.200.0/22'
  '103.31.4.0/22'
  '141.101.64.0/18'
  '108.162.192.0/18'
  '190.93.240.0/20'
  '188.114.96.0/20'
  '197.234.240.0/22'
  '198.41.128.0/17'
  '162.158.0.0/15'
  '104.16.0.0/13'
  '104.24.0.0/14'
  '172.64.0.0/13'
  '131.0.72.0/22'
]
param budgetAmount = 1000
param budgetStartDate = '2026-10-01'
param budgetContactEmails = ['admin@avisingh.dev']
