# Ensure errors stop the script
$ErrorActionPreference = "Stop"

Write-Host "Starting Minikube with Docker driver..."
minikube start --driver=docker

Write-Host "Configuring Docker to use Minikube's Docker daemon..."
Invoke-Expression (& minikube -p minikube docker-env --shell powershell | Out-String)

Write-Host "Minikube started and Docker environment configured."
Write-Host "Checking for Kubernetes namespace 'vehicle-platform'..."

try {
    $null = kubectl get namespace vehicle-platform 2>$null
    Write-Host "Namespace 'vehicle-platform' already exists."
} catch {
    Write-Host "Namespace 'vehicle-platform' not found. Creating it..."
    kubectl create namespace vehicle-platform
    Write-Host "Namespace 'vehicle-platform' created."
}
