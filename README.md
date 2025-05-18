# Vehicle Platform Kubernetes Deployment Guide

This guide explains how to deploy and manage the Vehicle Platform microservices using the provided Makefile commands.

## Prerequisites

- Minikube installed
- kubectl installed
- Docker installed
- Make installed
- Helm installed

## Quick Start

To get everything up and running quickly:

```bash
# Start Minikube
make start

# Deploy everything (namespace, services, and Docker containers)
make deploy-all

# Check status of deployments
make status
```

## Common Commands

Below are the most commonly used commands for day-to-day development and operations:

### Starting the Environment

```bash
# Start Minikube cluster
make start
```

### Deploying Services

```bash
# Deploy everything (complete setup)
make deploy-all

# Deploy just Kubernetes services (like message broker, API gateway)
make deploy-k8s

# Deploy just Docker services (microservices)
make deploy-docker

# Deploy vehicle stack (for all vehicles)
make vehicle-stack-deploy
```

### Updating Services After Code Changes

```bash
# Update a specific service (e.g., location-sender)
make location-sender
# or
make update-location-sender

# Update all services at once
make update-all
```

Each update creates a new timestamped version of the image (e.g., `service-name:20250515-123045`) and automatically updates the Kubernetes deployments.

### Viewing Status and Logs

```bash
# Check status of all services
make status

# View logs for a specific service
make logs-location-sender
```

### Port Forwarding (Accessing Services)

```bash
# Forward ports for a specific service
make forward-location-sender

# Forward RabbitMQ ports
make forward-mbroker

# Forward API Gateway ports
make forward-api-gateway
```

### Checking Versions

```bash
# See which image versions are currently deployed
make versions
```

### Cleanup

```bash
# Delete everything
make delete-all

# Delete just Docker services
make delete-docker

# Delete just Kubernetes services
make delete-k8s
```

## Service-Specific Commands

### Available Docker Services

These services can be updated/deployed individually using `make <service-name>`:

- `location-tracker`
- `central-director`
- `visor`

> **Note:** Other microservices (`datamock`, `distance-monitor`, `emergency-brake`, `location-sender`) are built and deployed as part of the `make vehicle-stack-deploy` command, not as individual Makefile targets.

### Available Kubernetes Services

These infrastructure services can be deployed individually:

- `make deploy-mbroker` - Deploy RabbitMQ message broker
- `make deploy-api-gateway` - Deploy Kong API Gateway
- `make deploy-datamock` - Deploy data mock service

> **Tip:** Use `make deploy-k8s` to deploy all Kubernetes infrastructure services at once.

### Vehicle Stack Configuration

The number of vehicles to deploy can be specified in the Makefile by editing the `VEHICLES` variable:

```makefile
VEHICLES = vehicle-1 vehicle-2
```

Add or remove vehicle names in this list to control how many vehicle stacks are deployed with `make vehicle-stack-deploy`.

## Development Workflow

1. Make code changes to a microservice
2. Run `make <service-name>` to build and deploy your changes
3. Check logs with `make logs-<service-name>`
4. Access service with `make forward-<service-name>`

When you run `make <service-name>`, the system automatically:
- Builds a new Docker image with a timestamp-based version
- Updates the Kubernetes deployment file with the new version
- Applies the changes to the Kubernetes cluster

## Versioning

The system uses timestamp-based versioning (YYYYMMDD-HHMMSS) for Docker images. This ensures each build is uniquely identifiable and makes rollbacks easier if needed.

To see current versions in use:
```bash
make versions
```

## Troubleshooting

If services aren't updating:
1. Check image versions with `make versions`
2. Verify pods are running with `make status`
3. Check logs with `make logs-<service-name>`

If you need to force a restart of a service:
```bash
kubectl rollout restart deployment <service-name> -n backend
```
