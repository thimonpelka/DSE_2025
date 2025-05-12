#!/bin/bash

# Start Minikube using the Docker driver
minikube start --driver=docker

# Use Minikube's Docker daemon so local builds are visible to the cluster
eval $(minikube -p minikube docker-env)
