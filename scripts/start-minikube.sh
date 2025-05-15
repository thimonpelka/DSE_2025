#!/bin/bash

set -e  # Exit on any error

echo "Starting Minikube with Docker driver..."
minikube start --driver=docker

echo "Configuring Docker environment to use Minikube's Docker daemon..."
eval $(minikube -p minikube docker-env)

echo "Checking for Kubernetes namespace 'vehicle-platform'..."
if ! kubectl get namespace vehicle-platform >/dev/null 2>&1; then
  echo "Namespace 'vehicle-platform' not found. Creating it..."
  kubectl create namespace vehicle-platform
  echo "Namespace 'vehicle-platform' created."
else
  echo "Namespace 'vehicle-platform' already exists."
fi

echo "Minikube started and namespace configured."
