using 'main.bicep'

param workload = 'palisade'
param environment = 'prod'
param resourceGroupName = 'rg-palisade-prod'
param pipelineIdentityName = 'id-palisade-pipeline-prod'
param webIdentityName = 'id-palisade-web-prod'
param apiIdentityName = 'id-palisade-api-prod'
param keyVaultName = 'kv-palisade-prod'
// GitHub issues the immutable subject (owner and repository ids) for repositories created after
// 2026-07-15; this one dates from 2026-10-05.
param federatedCredentials = [
  {
    name: 'github-actions-main'
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:avisinghdotdev@217426772/Palisade@1406459686:ref:refs/heads/main'
  }
]
param vaultOfficerPrincipalId = 'a4db2292-7f75-4aac-8b2c-74d57c9c181f'
// cravisingh, owned by the avisinghdotdev shared stack in az-sub-avisingh-shared.
param registrySubscriptionId = 'cc24a8b0-cb74-4ebb-8532-75e6064a349a'
param registryResourceGroupName = 'rg-avisingh-shared'
param registryName = 'cravisingh'
param registryRepositoryPrefix = 'palisade/'
