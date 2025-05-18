#!/bin/bash

set -e  # Exit on any error

echo "Starting Minikube with Docker driver..."
minikube start --driver=docker

echo "Configuring Docker environment to use Minikube's Docker daemon..."
eval $(minikube -p minikube docker-env)

echo "Checking for Kubernetes namespace 'backend'..."
if ! kubectl get namespace backend >/dev/null 2>&1; then
  echo "Namespace 'backend' not found. Creating it..."
  kubectl create namespace backend
  echo "Namespace 'backend' created."
else
  echo "Namespace 'backend' already exists."
fi

echo "Minikube started and namespace configured."
