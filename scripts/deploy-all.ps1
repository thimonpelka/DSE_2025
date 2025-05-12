# Ensure errors stop the script
$ErrorActionPreference = "Stop"

Write-Host "Applying shared Kubernetes manifests (e.g., namespace, ingress)..."
kubectl apply -f "k8s/"

# Use a default username if $env:USERNAME is not set
$userName = if ($env:USERNAME) { $env:USERNAME.ToLower() } else { "dev" }

# Loop through all service folders
Get-ChildItem -Directory "services" | ForEach-Object {
    $servicePath = $_.FullName
    $serviceName = $_.Name
    $imageTag = "$userName-$serviceName".ToLower()

    Write-Host "`nBuilding Docker image: $imageTag"
    docker build -t $imageTag $servicePath

    Write-Host "Applying Kubernetes manifests for $serviceName"
    kubectl apply -f "$servicePath/k8s/"
}
