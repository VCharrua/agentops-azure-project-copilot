# Project Delivery Copilot - developer entry points.
# Every target works without Azure credentials unless its name starts with "azure-".
# On Windows without make, run the commands below directly (they are plain python/docker/az calls).

PYTHON ?= python
IMAGE ?= project-delivery-copilot:local
ENV ?= dev
LOCATION ?= swedencentral

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test eval check data data-check run docker-build docker-run \
        bicep-build azure-whatif azure-deploy clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the package with dev and Azure extras (editable)
	$(PYTHON) -m pip install -e ".[dev,azure]"

lint: ## Ruff lint + format check, mypy strict, synthetic-data drift check
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy
	$(PYTHON) scripts/generate_synthetic_data.py --check

format: ## Auto-format and apply safe lint fixes
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

typecheck: ## mypy strict
	$(PYTHON) -m mypy

test: ## Unit + integration tests with the coverage gate (85%)
	$(PYTHON) -m pytest --cov --cov-report=term-missing --cov-report=xml

eval: ## Offline agent evaluation against the golden dataset; fails below thresholds
	$(PYTHON) -m copilot.evals.runner --dataset evals/golden/v1.jsonl --thresholds evals/thresholds.yaml --out reports/evals

check: lint test eval ## Everything CI runs that needs no cloud access

data: ## Regenerate the synthetic dataset
	$(PYTHON) scripts/generate_synthetic_data.py

data-check: ## Fail if committed synthetic data differs from the generator
	$(PYTHON) scripts/generate_synthetic_data.py --check

run: ## Run the API locally with mocked Azure services (http://localhost:8000/docs)
	$(PYTHON) -m uvicorn copilot.api.main:app --reload --port 8000

docker-build: ## Build the container image
	docker build --build-arg APP_VERSION=$$(git rev-parse --short HEAD) -t $(IMAGE) .

docker-run: ## Run the container in local (mock) mode
	docker run --rm -p 8000:8000 $(IMAGE)

bicep-build: ## Compile and lint all Bicep (needs the Bicep CLI via `az bicep install`)
	az bicep build --file infra/main.bicep --stdout > /dev/null
	az bicep build-params --file infra/parameters/dev.bicepparam --stdout > /dev/null
	az bicep build-params --file infra/parameters/staging.bicepparam --stdout > /dev/null
	az bicep build-params --file infra/parameters/prod.bicepparam --stdout > /dev/null

azure-whatif: ## Preview infrastructure changes for ENV (needs az login)
	az deployment sub what-if --location $(LOCATION) --template-file infra/main.bicep \
	  --parameters infra/parameters/$(ENV).bicepparam

azure-deploy: ## Deploy infrastructure for ENV (needs az login)
	az deployment sub create --name copilot-$(ENV) --location $(LOCATION) --template-file infra/main.bicep \
	  --parameters infra/parameters/$(ENV).bicepparam

clean: ## Remove caches, local state and reports
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov reports .local
