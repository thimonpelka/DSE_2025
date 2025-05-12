# Ensure errors stop the script
$ErrorActionPreference = "Stop"

Write-Host "Starting Minikube with Docker driver..."
minikube start --driver=docker

Write-Host "Configuring Docker to use Minikube's Docker daemon..."
Invoke-Expression (& minikube -p minikube docker-env --shell powershell | Out-String)

Write-Host "Minikube started and Docker environment configured."
