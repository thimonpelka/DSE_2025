# DSE_2025

To use:

## Start minikube (if not already running)
make start

## Deploy everything
make deploy-all

## Build and Deploy only Docker services
make deploy-docker

## Deploy only Kubernetes services
make deploy-k8s

## Only Build Docker images (This does not deploy them to Kubernetes!!)
make build-docker

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

# Additions

If you add any new services, you have to add them in the Makefile to DOCKER_SERVICES or K8S_SERVICES or otherwise they wont get built using the Makefile commands.
