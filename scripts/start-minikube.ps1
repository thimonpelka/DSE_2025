# Ensure errors stop the script
$ErrorActionPreference = "Stop"

Write-Host "Starting Minikube with Docker driver..."
minikube start --driver=docker

Write-Host "Configuring Docker to use Minikube's Docker daemon..."
Invoke-Expression (& minikube -p minikube docker-env --shell powershell | Out-String)

Write-Host "Minikube started and Docker environment configured."
Write-Host "Checking for Kubernetes namespace 'backend'..."

try {
    $null = kubectl get namespace backend 2>$null
    Write-Host "Namespace 'backend' already exists."
} catch {
    Write-Host "Namespace 'backend' not found. Creating it..."
    kubectl create namespace backend
    Write-Host "Namespace 'backend' created."
}
