#!/bin/bash

# Ensure Docker is pointed to Minikube
eval "$(minikube -p minikube docker-env)"

# Apply global Kubernetes configs (like namespace, ingress)
kubectl apply -f k8s/

# Fallback if USER is not set
USER_NAME=${USER:-dev}

# Deploy all service folders
for service in services/*; do
  SERVICE_NAME=$(basename "$service")
  IMAGE_TAG="${USER_NAME}-${SERVICE_NAME}"

  echo "Building image: $IMAGE_TAG"
  docker build -t "$IMAGE_TAG" "$service"

  echo "Applying Kubernetes manifests for $SERVICE_NAME"
  kubectl apply -f "$service/k8s/"
done
