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

.PHONY: start deploy delete

start:
	$(SHELL_SCRIPT) $(START_SCRIPT)

deploy:
	$(SHELL_SCRIPT) $(DEPLOY_SCRIPT)
