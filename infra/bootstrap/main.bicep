// One-time bootstrap of the subscription: the resource group, the identities, the vault that holds
// the secrets before the first release, the rights the release workflow needs, and Palisade's
// repository rights on the shared registry. Run from a laptop with Owner on both subscriptions;
// everything else is deployed by the workflow.
targetScope = 'subscription'

param workload string

@allowed(['prod'])
param environment string

param location string = 'centralindia'

param resourceGroupName string

param pipelineIdentityName string

param webIdentityName string

param apiIdentityName string

param keyVaultName string

@description('Trust from GitHub Actions: one credential per workflow ref.')
param federatedCredentials { name: string, issuer: string, subject: string }[]

@description('Object ID of the person who enters the secrets and imports the origin certificate.')
param vaultOfficerPrincipalId string

@description('The shared registry the images are pushed to, in another subscription.')
param registrySubscriptionId string

param registryResourceGroupName string

param registryName string

@description('Repository prefix Palisade owns inside the registry, with the trailing slash.')
param registryRepositoryPrefix string

var tags = { workload: workload, environment: environment }

var roleDefinitionIds = {
  contributor: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
  deploymentStackOwner: 'adb29209-aa1d-457b-a786-c913953d2891'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  keyVaultSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  keyVaultCertificatesOfficer: 'a4417e6f-fecd-4de8-b567-7b0420556985'
}

module workloadResourceGroup 'br/public:avm/res/resources/resource-group:0.4.4' = {
  params: {
    name: resourceGroupName
    location: location
    tags: tags
  }
}

module pipelineIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: pipelineIdentityName
    location: location
    tags: tags
    federatedIdentityCredentials: [
      for credential in federatedCredentials: {
        name: credential.name
        issuer: credential.issuer
        subject: credential.subject
        audiences: ['api://AzureADTokenExchange']
      }
    ]
  }
}

module webIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: webIdentityName
    location: location
    tags: tags
  }
}

module apiIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.6.0' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: apiIdentityName
    location: location
    tags: tags
  }
}

// The api app reads its secrets and the environment reads the origin certificate (stored as a
// secret) through the web identity; both need only the secrets-user role.
module keyVault 'br/public:avm/res/key-vault/vault:0.14.2' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: keyVaultName
    location: location
    tags: tags
    sku: 'standard'
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    roleAssignments: [
      {
        principalId: apiIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.keyVaultSecretsUser)
      }
      {
        principalId: webIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.keyVaultSecretsUser)
      }
      {
        principalId: vaultOfficerPrincipalId
        principalType: 'User'
        roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.keyVaultSecretsOfficer)
      }
      {
        principalId: vaultOfficerPrincipalId
        principalType: 'User'
        roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.keyVaultCertificatesOfficer)
      }
    ]
  }
}

module pipelineContributor 'br/public:avm/res/authorization/role-assignment/sub-scope:0.1.1' = {
  params: {
    principalId: pipelineIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.contributor)
  }
}

module pipelineDeploymentStackOwner 'br/public:avm/res/authorization/role-assignment/sub-scope:0.1.1' = {
  params: {
    principalId: pipelineIdentity.outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionIdOrName: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.deploymentStackOwner)
  }
}

module registryRepositoryRoles '../modules/registry-repository-roles.bicep' = {
  scope: resourceGroup(registrySubscriptionId, registryResourceGroupName)
  params: {
    registryName: registryName
    repositoryPrefix: registryRepositoryPrefix
    pushPrincipalIds: [pipelineIdentity.outputs.principalId]
    pullPrincipalIds: [webIdentity.outputs.principalId, apiIdentity.outputs.principalId]
  }
}

output pipelineIdentityClientId string = pipelineIdentity.outputs.clientId
output pipelineIdentityPrincipalId string = pipelineIdentity.outputs.principalId
output webIdentityResourceId string = webIdentity.outputs.resourceId
output apiIdentityResourceId string = apiIdentity.outputs.resourceId
