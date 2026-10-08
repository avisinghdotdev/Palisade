// Push and pull rights on one repository prefix of a registry another stack owns. The registry is
// only referenced; the role assignments are the sole resources this module creates.
targetScope = 'resourceGroup'

param registryName string

@description('Repository prefix the principals may use, with the trailing slash.')
param repositoryPrefix string

@description('Identities allowed to push images under the prefix: the workflow.')
param pushPrincipalIds string[]

@description('Identities allowed to pull images under the prefix: the container apps.')
param pullPrincipalIds string[]

var roleDefinitionIds = {
  repositoryReader: 'b93aa761-3e63-49ed-ac28-beffa264f7ac'
  repositoryWriter: '2a1e307c-b015-4ebd-883e-5b7698a07328'
}

// ABAC conditions scope a registry role to repositories under the prefix. Each role's data
// actions are negated first, so the prefix check applies to every action the role grants.
var readerConditionTemplate = '''
(
  (
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/content/read'})
    AND
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/metadata/read'})
  )
  OR
  (
    @Request[Microsoft.ContainerRegistry/registries/repositories:name] StringStartsWithIgnoreCase 'REPOSITORY_PREFIX'
  )
)
'''
var writerConditionTemplate = '''
(
  (
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/content/read'})
    AND
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/metadata/read'})
    AND
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/content/write'})
    AND
    !(ActionMatches{'Microsoft.ContainerRegistry/registries/repositories/metadata/write'})
  )
  OR
  (
    @Request[Microsoft.ContainerRegistry/registries/repositories:name] StringStartsWithIgnoreCase 'REPOSITORY_PREFIX'
  )
)
'''
var readerCondition = replace(readerConditionTemplate, 'REPOSITORY_PREFIX', repositoryPrefix)
var writerCondition = replace(writerConditionTemplate, 'REPOSITORY_PREFIX', repositoryPrefix)

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: registryName
}

resource pushAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in pushPrincipalIds: {
    name: guid(registry.id, principalId, roleDefinitionIds.repositoryWriter)
    scope: registry
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.repositoryWriter)
      condition: writerCondition
      conditionVersion: '2.0'
    }
  }
]

resource pullAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in pullPrincipalIds: {
    name: guid(registry.id, principalId, roleDefinitionIds.repositoryReader)
    scope: registry
    properties: {
      principalId: principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionIds.repositoryReader)
      condition: readerCondition
      conditionVersion: '2.0'
    }
  }
]
