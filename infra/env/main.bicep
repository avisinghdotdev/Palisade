// Per-environment stack: logs, the Container Apps environment, the two apps and the budget. The
// vault comes from the bootstrap and is only read here.
targetScope = 'subscription'

param workload string

@allowed(['prod'])
param environment string

param location string = 'centralindia'
param resourceGroupName string
param logAnalyticsName string
param keyVaultName string
param containerAppsEnvironmentName string
param webAppName string
param apiAppName string

@description('Resource ID of the web app identity; it also reads the origin certificate for the environment.')
param webIdentityResourceId string

@description('Resource ID of the api app identity; it reads the secrets.')
param apiIdentityResourceId string

param registryLoginServer string

@description('Image tag built by the workflow, the git SHA.')
param imageTag string

@description('Revision name suffix: release version and run number, lowercase with hyphens.')
param revisionSuffix string

@description('Cloudflare IPv4 ranges. Empty means the apps accept traffic from anywhere.')
param allowedInboundCidrs string[] = []

@description('Hostnames bound to the web app. Empty until the origin certificate exists in the vault.')
param customDomains string[] = []

@description('Name of the origin certificate in the vault. Empty until it has been imported.')
param originCertificateName string = ''

param budgetAmount int
param budgetStartDate string
param budgetContactEmails string[]

var tags = { workload: workload, environment: environment }
var originCertificateResourceName = 'cert-${workload}-origin-${environment}'
var hasOriginCertificate = originCertificateName != ''

// Vault secrets the api reads, by environment variable. Versionless URLs, so a rotated secret
// reaches the running revision without a deployment.
var apiSecrets = [
  { env: 'PAYPAL_CLIENT_ID', secret: 'paypal-client-id' }
  { env: 'PAYPAL_CLIENT_SECRET', secret: 'paypal-client-secret' }
  { env: 'PAYPAL_BUYER_CLIENT_ID', secret: 'paypal-buyer-client-id' }
  { env: 'PAYPAL_BUYER_CLIENT_SECRET', secret: 'paypal-buyer-client-secret' }
  { env: 'PAYPAL_WEBHOOK_ID', secret: 'paypal-webhook-id' }
  { env: 'ANTHROPIC_API_KEY', secret: 'anthropic-api-key' }
  { env: 'DATABASE_URL', secret: 'database-url' }
]
var apiSecretEnvironment = [for item in apiSecrets: { name: item.env, secretRef: item.secret }]
var apiEnvironment = [
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'LOG_FORMAT', value: 'json' }
  // Startup applies pending migrations under an advisory lock, so a second replica waits.
  { name: 'RUN_MIGRATIONS', value: 'true' }
  // Every replica runs the job worker; the queue hands each job to one of them.
  { name: 'RUN_WORKER', value: 'true' }
]

resource keyVault 'Microsoft.KeyVault/vaults@2026-02-01' existing = {
  scope: resourceGroup(resourceGroupName)
  name: keyVaultName
}

module workloadResourceGroup 'br/public:avm/res/resources/resource-group:0.4.4' = {
  params: {
    name: resourceGroupName
    location: location
    tags: tags
  }
}

module logAnalytics 'br/public:avm/res/operational-insights/workspace:0.16.1' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: logAnalyticsName
    location: location
    tags: tags
    dataRetention: 30
  }
}

module containerAppsEnvironment 'br/public:avm/res/app/managed-environment:0.16.0' = {
  scope: resourceGroup(resourceGroupName)
  dependsOn: [workloadResourceGroup]
  params: {
    name: containerAppsEnvironmentName
    location: location
    tags: tags
    zoneRedundant: false
    publicNetworkAccess: 'Enabled'
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    // The log-analytics destination posts with the workspace shared key, which a workspace with
    // local authentication off rejects silently. The Azure Monitor route lands the same logs in
    // the resource-specific tables ContainerAppConsoleLogs and ContainerAppSystemLogs.
    appLogsConfiguration: { destination: 'azure-monitor' }
    diagnosticSettings: [
      {
        name: 'log-analytics'
        workspaceResourceId: logAnalytics.outputs.resourceId
        logAnalyticsDestinationType: 'Dedicated'
        logCategoriesAndGroups: [
          { category: 'ContainerAppConsoleLogs' }
          { category: 'ContainerAppSystemLogs' }
        ]
      }
    ]
    managedIdentities: { userAssignedResourceIds: [webIdentityResourceId] }
    certificate: hasOriginCertificate
      ? {
          name: originCertificateResourceName
          certificateKeyVaultProperties: {
            identityResourceId: webIdentityResourceId
            keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${originCertificateName}'
          }
        }
      : null
  }
}

module apiApp 'br/public:avm/res/app/container-app:0.23.0' = {
  scope: resourceGroup(resourceGroupName)
  params: {
    name: apiAppName
    location: location
    tags: tags
    environmentResourceId: containerAppsEnvironment.outputs.resourceId
    workloadProfileName: 'Consumption'
    managedIdentities: { userAssignedResourceIds: [apiIdentityResourceId] }
    registries: [{ server: registryLoginServer, identity: apiIdentityResourceId }]
    secrets: [
      for item in apiSecrets: {
        name: item.secret
        keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${item.secret}'
        identity: apiIdentityResourceId
      }
    ]
    ingressExternal: false
    ingressTargetPort: 8000
    // Traffic from the web app stays inside the environment; a redirect to HTTPS would break the proxy.
    ingressAllowInsecure: true
    revisionSuffix: revisionSuffix
    scaleSettings: { minReplicas: 0, maxReplicas: 2 }
    containers: [
      {
        name: 'api'
        image: '${registryLoginServer}/${workload}/api:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: concat(apiEnvironment, apiSecretEnvironment)
      }
    ]
  }
}

module webApp 'br/public:avm/res/app/container-app:0.23.0' = {
  scope: resourceGroup(resourceGroupName)
  params: {
    name: webAppName
    location: location
    tags: tags
    environmentResourceId: containerAppsEnvironment.outputs.resourceId
    workloadProfileName: 'Consumption'
    managedIdentities: { userAssignedResourceIds: [webIdentityResourceId] }
    registries: [{ server: registryLoginServer, identity: webIdentityResourceId }]
    ingressExternal: true
    ingressTargetPort: 8080
    ingressAllowInsecure: false
    ipSecurityRestrictions: [
      for (cidr, i) in allowedInboundCidrs: {
        name: 'cloudflare-${i}'
        ipAddressRange: cidr
        action: 'Allow'
      }
    ]
    customDomains: [
      for domain in customDomains: {
        name: domain
        certificateId: '${containerAppsEnvironment.outputs.resourceId}/certificates/${originCertificateResourceName}'
        bindingType: 'SniEnabled'
      }
    ]
    revisionSuffix: revisionSuffix
    scaleSettings: { minReplicas: 0, maxReplicas: 2 }
    containers: [
      {
        name: 'web'
        image: '${registryLoginServer}/${workload}/web:${imageTag}'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: [{ name: 'API_UPSTREAM', value: apiApp.outputs.name }]
      }
    ]
  }
}

module budget '../modules/budget.bicep' = {
  params: {
    name: 'budget-${workload}-${environment}'
    amount: budgetAmount
    startDate: budgetStartDate
    contactEmails: budgetContactEmails
  }
}

output webFqdn string = webApp.outputs.fqdn
