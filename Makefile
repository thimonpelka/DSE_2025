# Detect platform
UNAME_S := $(shell uname -s)
# Windows detection (Git Bash or native PowerShell)
ifeq ($(OS),Windows_NT)
	IS_WINDOWS := true
else
	IS_WINDOWS := false
endif

# Commands
ifeq ($(IS_WINDOWS),true)
	SHELL_SCRIPT := powershell -ExecutionPolicy Bypass -File
	START_SCRIPT := scripts/start-minikube.ps1
	DEPLOY_SCRIPT := scripts/deploy-all.ps1
else
	SHELL_SCRIPT := sh
	START_SCRIPT := scripts/start-minikube.sh
	DEPLOY_SCRIPT := scripts/deploy-all.sh
endif

# Namespace
NAMESPACE := vehicle-platform

# Docker build settings
# DOCKER_BUILD_CMD := eval $(minikube docker-env) && docker build
DOCKER_BUILD_CMD := docker build
DOCKER_LOAD_CMD := :
ifeq ($(IS_WINDOWS),true)
	DOCKER_BUILD_CMD := docker build
	# DOCKER_LOAD_CMD := minikube image load
else
	DOCKER_BUILD_CMD := eval $$(minikube docker-env) && docker build
	DOCKER_LOAD_CMD := :
endif

DOCKER_SERVICES := location-sender location-tracker distance-monitor \
                   emergency-break central-director visor

# Kubernetes configuration services
K8S_SERVICES := mbroker api-gateway datamock

.PHONY: start deploy delete build-docker deploy-docker \
		deploy-k8s build-all deploy-all delete-all \
		$(DOCKER_SERVICES) $(K8S_SERVICES)

# Start minikube
start:
	$(SHELL_SCRIPT) $(START_SCRIPT)

# Build all Docker services
build-docker: $(DOCKER_SERVICES)

# Deploy all Docker services to Kubernetes
deploy-docker: build-docker
	@for service in $(DOCKER_SERVICES); do \
		echo "Deploying $$service..."; \
		kubectl apply -f services/$$service/k8s/deployment.yaml; \
		kubectl apply -f services/$$service/k8s/service.yaml; \
	done

# Build individual Docker services
$(DOCKER_SERVICES):
	@echo "Building $@ service..."
	$(DOCKER_BUILD_CMD) -t $@-service:latest services/$@
ifeq ($(IS_WINDOWS),true)
	@echo "Loading $@ into Minikube..."
	$(DOCKER_LOAD_CMD) $@-service:latest
endif

# Deploy Kubernetes-specific services
deploy-k8s:
	@for service in $(K8S_SERVICES); do \
		echo "Deploying $$service..."; \
		$(MAKE) deploy-$$service; \
	done

# Deploy namespace first
deploy-namespace:
	kubectl apply -f k8s/namespace.yaml

# Deployment targets for Kubernetes services
deploy-mbroker:
	kubectl apply -f services/mbroker/k8s/secret.yaml
	kubectl apply -f services/mbroker/k8s/deployment.yaml
	kubectl apply -f services/mbroker/k8s/service.yaml

deploy-api-gateway:
	kubectl apply -f services/passenger-api-gateway/k8s/kong-deployment.yaml
	# kubectl apply -f services/passenger-api-gateway/k8s/kong-service.yaml
	kubectl apply -f services/passenger-api-gateway/k8s/kong-config.yaml

deploy-datamock:
	kubectl apply -f services/datamock/k8s/deployment.yaml
	kubectl apply -f services/datamock/k8s/service.yaml

# Comprehensive deploy target
deploy-all: deploy-namespace deploy-k8s deploy-docker

# Delete targets
delete-docker:
	@for service in $(DOCKER_SERVICES); do \
		echo "Deleting $$service..."; \
		kubectl delete -f services/$$service/k8s/service.yaml; \
		kubectl delete -f services/$$service/k8s/deployment.yaml; \
	done

delete-k8s:
	@for service in $(K8S_SERVICES); do \
		echo "Deleting $$service..."; \
		$(MAKE) delete-$$service; \
	done

delete-mbroker:
	kubectl delete -f services/mbroker/k8s/service.yaml
	kubectl delete -f services/mbroker/k8s/deployment.yaml
	kubectl delete -f services/mbroker/k8s/secret.yaml

delete-api-gateway:
	kubectl delete -f services/passenger-api-gateway/k8s/kong-service.yaml
	kubectl delete -f services/passenger-api-gateway/k8s/kong-deployment.yaml
	kubectl delete -f services/passenger-api-gateway/k8s/kong-config.yaml

delete-datamock:
	kubectl delete -f services/datamock/k8s/service.yaml
	kubectl delete -f services/datamock/k8s/deployment.yaml

# Delete everything
delete-all: delete-k8s delete-docker

# Status and troubleshooting
status:
	@echo -e "\nStatus of all services in the $(NAMESPACE) namespace:\n"
	kubectl get namespaces
	@echo -e "\n"
	kubectl get deployments -n $(NAMESPACE)
	@echo -e "\n"
	kubectl get services -n $(NAMESPACE)
	@echo -e "\n"
	kubectl get pods -n $(NAMESPACE)

# Logs for services
logs-%:
	kubectl logs -n $(NAMESPACE) -l app=$*

# Port forwarding
forward-%:
	@case "$*" in \
		mbroker) \
			kubectl port-forward -n $(NAMESPACE) service/rabbitmq 5672:5672 15672:15672 \
			;; \
		api-gateway) \
			kubectl port-forward -n $(NAMESPACE) service/kong-proxy 8000:80 8444:8444 \
			;; \
		datamock) \
			kubectl port-forward -n $(NAMESPACE) service/datamock-service 8000:8000 \
			;; \
		*) \
			kubectl port-forward -n $(NAMESPACE) service/$*-service 8000:8000 \
			;; \
	esac
