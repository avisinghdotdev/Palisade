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
// Set together once the Cloudflare records exist: the hostname and the ranges from
// https://www.cloudflare.com/ips-v4.
param customDomains = []
param allowedInboundCidrs = []
param budgetAmount = 1000
param budgetStartDate = '2026-10-01'
param budgetContactEmails = ['admin@avisingh.dev']
