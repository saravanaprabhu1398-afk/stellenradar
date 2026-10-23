# Stellenradar – developer commands (GNU Make 3.81 compatible)
TF_DIR := infra/envs/dev

.PHONY: help venv lint test tf-fmt tf-init tf-plan tf-apply bootstrap-ci github-setup invoke-ingest-smoke

help:
	@echo "make venv          Create .venv with Python 3.12 and dev tools"
	@echo "make lint          Ruff lint + format check"
	@echo "make test          Unit tests with coverage"
	@echo "make invoke-ingest-smoke  Small real ingest run in AWS (1 term, 3 details)"
	@echo "make tf-fmt        Format all Terraform code"
	@echo "make tf-plan       Init and plan the dev environment locally (writes dev.tfplan)"
	@echo "make tf-apply      Apply the saved dev.tfplan"
	@echo "make bootstrap-ci  One-time: create the GitHub OIDC roles from your machine"
	@echo "make github-setup  One-time: set GitHub variables, secret and the dev environment (needs gh)"

venv:
	python3.12 -m venv .venv
	.venv/bin/pip install -q -r requirements-dev.txt

lint:
	.venv/bin/ruff check src tests spikes
	.venv/bin/ruff format --check src tests

test:
	.venv/bin/pytest --cov --cov-report=term-missing

# Writes real (small) raw data to S3; safe to repeat. Output in ingest-smoke.json (git-ignored).
invoke-ingest-smoke:
	aws lambda invoke --function-name stellenradar-dev-ingest --cli-binary-format raw-in-base64-out \
	  --cli-read-timeout 900 \
	  --payload '{"run_id":"smoke-$(shell date -u +%Y%m%dT%H%M%SZ)","run_date":"$(shell date -u +%Y-%m-%d)","search_terms":["ETL Entwickler"],"max_details":3}' \
	  ingest-smoke.json
	@cat ingest-smoke.json; echo

tf-fmt:
	terraform fmt -recursive infra

tf-init:
	terraform -chdir=$(TF_DIR) init -backend-config=backend.hcl

tf-plan: tf-init
	terraform -chdir=$(TF_DIR) plan -out=dev.tfplan

tf-apply:
	terraform -chdir=$(TF_DIR) apply dev.tfplan
	rm -f $(TF_DIR)/dev.tfplan

# CI cannot create its own access, so the OIDC roles are applied once locally.
bootstrap-ci: tf-init
	terraform -chdir=$(TF_DIR) apply
	@echo ""
	@terraform -chdir=$(TF_DIR) output

github-setup:
	./scripts/github-setup.sh
