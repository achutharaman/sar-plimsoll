SHELL := /bin/bash
PY    := .venv/bin/python
VENV  := .venv/bin

PROJECT ?= $(shell gcloud config get-value project 2>/dev/null)
REGION  ?= us-central1
TAG     ?= $(shell date +%Y%m%d-%H%M%S)
IMAGE    = $(REGION)-docker.pkg.dev/$(PROJECT)/plimsoll/app:$(TAG)
TF       = terraform -chdir=infra
# Re-applies keep the last deployed image instead of reverting to the placeholder.
IMAGE_REF ?= $(shell cat .last-image 2>/dev/null)

.PHONY: help install lint fmt test test-one run-api run-worker review rules-ingest rules-ingest-job rules-status \
        web-install web-dev web-build web-deploy seed smoke benchmark \
        tf-init tf-plan tf-apply build deploy bootstrap

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install:  ## Create .venv and install with dev extras
	python3.12 -m venv .venv
	$(VENV)/pip install -q --upgrade pip
	$(VENV)/pip install -q -e ".[dev]"

lint:  ## Ruff lint + format check
	$(VENV)/ruff check src tests scripts
	$(VENV)/ruff format --check src tests scripts

fmt:  ## Apply ruff fixes and formatting
	$(VENV)/ruff check --fix src tests
	$(VENV)/ruff format src tests

test:  ## Run the full test suite (offline)
	$(VENV)/pytest

test-one:  ## Run one test: make test-one T=tests/scoring/test_properties.py::test_order_does_not_matter
	$(VENV)/pytest $(T) -vv

run-api:  ## Run the API locally on :8080 (offline wiring unless .env says otherwise)
	$(VENV)/uvicorn sar_plimsoll.api.app:app --reload --port 8080

run-worker:  ## Run the worker locally on :8081
	$(VENV)/uvicorn sar_plimsoll.worker.app:app --reload --port 8081

review:  ## Review a file directly: make review FILE=path [RULES=rules.csv]
	$(VENV)/plimsoll review $(FILE) $(if $(RULES),--rules $(RULES))

rules-ingest:  ## CSV → BigQuery + embeddings + index, from cold: make rules-ingest CSV=path
	@test -n "$(CSV)" || (echo "usage: make rules-ingest CSV=path/to/rules.csv" && exit 2)
	$(VENV)/plimsoll rules ingest $(CSV)

rules-ingest-job:  ## Same, as a Cloud Run Job execution (GCP): make rules-ingest-job CSV=path
	@test -n "$(CSV)" || (echo "usage: make rules-ingest-job CSV=path/to/rules.csv" && exit 2)
	PLIMSOLL_INGEST_BACKEND=cloudrun_job $(VENV)/plimsoll rules ingest $(CSV) --job

rules-status:  ## Show the current rules corpus version
	$(VENV)/plimsoll rules status

web-install:  ## Install dashboard dependencies (Node 22, see .nvmrc)
	cd web && npm ci || npm install

web-dev:  ## Dashboard dev server on :5173, proxying the API on :8080
	cd web && npm run dev

web-build:  ## Type-check and build the dashboard
	cd web && npm run build

web-deploy: web-build  ## Deploy the dashboard to Firebase Hosting (uses gcloud application-default credentials)
	cd web && GOOGLE_APPLICATION_CREDENTIALS=$$HOME/.config/gcloud/application_default_credentials.json \
	  npx -y firebase-tools@15 deploy --only hosting --project $(PROJECT) --non-interactive

HOSTING_URL = https://$(PROJECT).web.app
WEB_KEY     = $(shell grep '^firebase_web_api_key' infra/terraform.tfvars 2>/dev/null | cut -d'"' -f2)

seed:  ## Load demo rules and reviews through the API (PLIMSOLL_DEMO_EMAIL/PASSWORD in .env)
	@set -a && . ./.env && set +a && $(PY) scripts/seed.py --api $(HOSTING_URL) \
	  --email "$$PLIMSOLL_DEMO_EMAIL" --password "$$PLIMSOLL_DEMO_PASSWORD" --api-key "$(WEB_KEY)"

smoke:  ## Pre-demo check: health, sign-in, review, identical cached resubmission, analytics
	@set -a && . ./.env && set +a && cd scripts && ../$(PY) smoke.py --api $(HOSTING_URL) \
	  --email "$$PLIMSOLL_DEMO_EMAIL" --password "$$PLIMSOLL_DEMO_PASSWORD" --api-key "$(WEB_KEY)"

benchmark:  ## Paired two-tier vs all-Pro benchmark: make benchmark FILES="a.py b.ts"
	$(VENV)/plimsoll benchmark $(FILES)

tf-init:  ## terraform init
	$(TF) init

tf-plan:  ## terraform plan (needs PROJECT)
	$(TF) plan -var project_id=$(PROJECT) -var region=$(REGION) $(if $(IMAGE_REF),-var image=$(IMAGE_REF))

tf-apply:  ## terraform apply (uses .last-image if present, or IMAGE_REF=...)
	$(TF) apply -var project_id=$(PROJECT) -var region=$(REGION) $(if $(IMAGE_REF),-var image=$(IMAGE_REF))

build:  ## Build and push the image with Cloud Build (no local Docker needed)
	gcloud builds submit --project $(PROJECT) --region $(REGION) --tag $(IMAGE) .
	@echo $(IMAGE) > .last-image

deploy: build  ## Build, then roll the Cloud Run services and job to the new image
	$(MAKE) tf-apply IMAGE_REF=$$(cat .last-image)

bootstrap: tf-init tf-apply deploy web-deploy  ## Cold start: infra → image → services → dashboard
