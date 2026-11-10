# Makefile

.PHONY: help dev-products up down infra infra-staging bootstrap-github-repo observability-up observability-down lint format dev-split discovery-repo-vars discovery-repo-vars-plan

help:
	@echo "RAG Platform Commands:"
	@echo ""
	@echo "  Local Development:"
	@echo "    make install       - Install Python dependencies"
	@echo "    make up            - Start local DBs (Docker)"
	@echo "    make init          - Initialize local DBs, collections, indexes"
	@echo "    make dev           - Run FastAPI server locally (hot reload)"
	@echo "    make dev-support-web - Run the support web product on port 5173"
	@echo "    make dev-analytics-api - Run the analytics API on port 8090"
	@echo "    make dev-analytics-web - Run the analytics web product on port 5174"
	@echo "    make analytics-reference-smoke - Run both governed smoke journeys (fakes only)"
	@echo "    make analytics-reference-up - Serve the governed v2 API locally with fakes (port 8095)"
	@echo "    make analytics-golden - Run the M4 golden suite and write its report (fakes only)"
	@echo "    make analytics-triage-eval - Run the ADS-051 triage corpus and write its report (fakes only)"
	@echo "    make analytics-proposals-eval - Run the ADS-052 proposal corpus and write its report (fakes only)"
	@echo "    make analytics-dbt-eval - Run the ADS-053 dbt proposal corpus and write its report (fakes only)"
	@echo "    make analytics-prompts-eval - Run the ADS-054 prompt registry corpus and write its report (fakes only)"
	@echo "    make analytics-review-eval - Run the ADS-055 review workflow corpus and write its report (fakes only)"
	@echo "    make analytics-evaluation-eval - Run the ADS-056 candidate evaluation corpus and write its report (fakes only)"
	@echo "    make analytics-rollout-eval - Run the ADS-057 rollout corpus and write its report (fakes only)"
	@echo "    make analytics-drift-eval - Run the ADS-058 drift corpus and write its report (synthetic windows)"
	@echo "    make analytics-poisoning-eval - Run the ADS-059 poisoning corpus and write its report (fakes only)"
	@echo "    make analytics-security-eval - Run the ADS-067 security corpus and findings register and write its report (fakes only)"
	@echo "    make analytics-resilience-drill - Run the ADS-068 restore and failover drills and write their report (local, fakes only)"
	@echo "    make dev-products  - Run both products as Docker deployables"
	@echo "    make observability-up - Start Grafana, Prometheus, exporters, and admin UIs"
	@echo "    make observability-down - Stop the local operations plane"
	@echo "    make support-demo  - Validate Resolution Intelligence demo workflow"
	@echo "    make demo-ready-local - Start local deps, seed demo data, and run demo acceptance"
	@echo "    make ingest FILE=x - Ingest a file, directory, or --sample"
	@echo "    make down          - Stop local DBs (Docker only)"
	@echo "    make stop          - Stop everything (API + Docker + stale processes)"
	@echo "    make test          - Run tests"
	@echo ""
	@echo "  AWS Deployment:"
	@echo "    make deploy-aws       - Full deploy: Terraform + Build + Bootstrap (first time, prod)"
	@echo "    make infra            - Apply Terraform (prod)"
	@echo "    make infra-staging    - Apply Terraform (staging)"
	@echo "    make build            - Build & push Docker image to ECR"
	@echo "    make bootstrap        - Bootstrap EKS cluster (prod)"
	@echo "    make bootstrap-staging- Bootstrap EKS cluster (staging)"
	@echo "    make init-cloud       - Initialize cloud databases (prod)"
	@echo "    make deploy           - Helm upgrade API only (prod, code changes)"
	@echo "    make deploy-staging   - Helm upgrade API only (staging, code changes)"
	@echo "    make verify           - Full post-deploy verify (pods + DB init + tests + UI)"
	@echo "    make smoke-test       - Run smoke tests only"
	@echo "    make destroy          - Tear down ALL AWS resources (prod)"
	@echo ""
	@echo "  Azure Deployment:"
	@echo "    make deploy-azure      - Full deploy: Terraform + Build + Bootstrap (first time)"
	@echo "    make infra-azure       - Apply Azure Terraform only"
	@echo "    make build-azure       - Build & push Docker image to ACR"
	@echo "    make bootstrap-azure   - Bootstrap AKS cluster (K8s resources)"
	@echo "    make deploy-api-azure  - Helm upgrade API only (code changes)"
	@echo "    make deploy-analytics-azure - Build and deploy analytics API + web"
	@echo "    make deploy-landing-azure - Build and publish public landing to Azure Static Web Apps"
	@echo "    make pause-azure       - Pause billing: stop Postgres, delete Redis, release IPs, prune ACR, stop App Service, trim Log Analytics (~\$$19/day saved)"
	@echo "    make resume-azure      - Resume: start Postgres + App Service back up (Redis recreated on next deploy)"
	@echo "    make import-azure      - Import manually-created resources into Terraform state (run once)"
	@echo "    make destroy-azure     - Tear down ALL Azure resources + verify"
	@echo "    make discovery-repo-vars-plan - Show the GitHub repo variables the Discovery workflows need (from terraform output)"
	@echo "    make discovery-repo-vars      - Set them (idempotent; uses your gh session)"
	@echo "    make azure-dev-up      - Provision/update remote Docker development VM and start Compose"
	@echo "    make azure-dev-sync    - Sync this checkout to the remote Docker VM"
	@echo "    make azure-dev-start   - Start the VM and remote Compose services"
	@echo "    make azure-dev-stop    - Deallocate the VM while preserving Docker volumes"
	@echo "    make azure-dev-status  - Show remote Docker VM status"
	@echo "    make azure-dev-ssh     - Open an SSH shell on the remote Docker VM"
	@echo "    make azure-dev-tunnel  - Forward local app/API/search ports over SSH"
	@echo "    make azure-dev-destroy - Destroy the isolated remote-dev environment"
	@echo ""
	@echo "  Developer Setup:"
	@echo "    make setup         - Install deps + pre-commit hooks"
	@echo "    make lint          - Run ruff linter"
	@echo "    make format        - Auto-fix lint + format code"
	@echo "    make architecture-check - Validate Archify diagram sources at showcase quality"
	@echo "    make architecture-build - Validate and regenerate interactive architecture HTML"
	@echo ""
	@echo "  Split-Plane Development:"
	@echo "    make dev-control-plane  - Run control plane locally"
	@echo "    make dev-data-plane     - Run data plane locally"
	@echo "    make dev-split          - Run both planes via Docker Compose"
	@echo "    make test-control-plane - Run control plane tests"
	@echo "    make test-data-plane    - Run data plane tests"
	@echo "    make test-all           - Run all tests (monolith + split)"
	@echo ""
	@echo "  Quick start (local):  make setup && make up && make init && make dev"
	@echo "  Quick start (AWS):    make deploy-aws && make verify"
	@echo "  Quick start (Azure):  make deploy-azure"
	@echo ""
	@echo "  Post-Destroy Verification:"
	@echo "    make verify-cleanup              - Check for orphaned AWS resources"
	@echo "    make verify-cleanup-delete       - Check + delete orphaned AWS resources"
	@echo "    make verify-cleanup-azure        - Check for orphaned Azure resources"
	@echo "    make verify-cleanup-azure-delete - Check + delete orphaned Azure resources"
	@echo ""
	@echo "  Quick start (split):  make up && make dev-split"

# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

lint:
	ruff check

format:
	ruff check --fix && ruff format

up:
	docker compose up -d

observability-up:
	docker compose --profile observability up -d

observability-down:
	docker compose --profile observability down

down:
	docker compose down

dev-products:
	docker compose --profile products up --build

# Terraform only: provision VPC, EKS, Aurora, Redis, S3
infra:
	cd infra/terraform && terraform init -upgrade && terraform apply

# Initialize cloud databases (Qdrant collections, Neo4j indexes)
# Plan the repository settings stack (auto-merge, branch cleanup, CI gate ruleset). APPLY=1 applies it.
bootstrap-github-repo:
	@APPLY=$(APPLY) ./scripts/bootstrap_github_repo.sh

# Terraform for staging (isolated state key + staging.tfvars)
infra-staging:
	cd infra/terraform && \
	terraform init -upgrade -reconfigure \
	    -backend-config="key=staging/terraform.tfstate" && \
	terraform apply -var-file=envs/staging.tfvars

# Run both planes via Docker Compose (with all dependencies)
dev-split:
	docker compose --profile split up -d

# Discovery pilot: set the GitHub repository variables the Discovery workflows read from `terraform output`
# (identifiers and names only, no secrets; uses your own `gh` session). Idempotent. Run after `terraform apply`.
discovery-repo-vars-plan:
	DRY_RUN=1 ./scripts/discovery_repo_vars.sh

discovery-repo-vars:
	./scripts/discovery_repo_vars.sh
