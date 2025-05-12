# DSE_2025

To use:

## Start minikube (if not already running)
make start

## Deploy everything
make deploy-all

## Build only Docker services
make build-docker

## Deploy only Kubernetes services
make deploy-k8s

## Check status of all services
make status

## Delete everything
make delete-all

## Get logs for a specific service
make logs-location-sender

## Forward ports for a specific service
make forward-location-sender

## Show help
make help
