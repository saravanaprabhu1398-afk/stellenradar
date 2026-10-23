# Stellenradar – developer commands (GNU Make 3.81 compatible)
TF_DIR := infra/envs/dev

.PHONY: help tf-fmt tf-init tf-plan tf-apply bootstrap-ci github-setup

help:
	@echo "make tf-fmt        Format all Terraform code"
	@echo "make tf-plan       Init and plan the dev environment locally (writes dev.tfplan)"
	@echo "make tf-apply      Apply the saved dev.tfplan"
	@echo "make bootstrap-ci  One-time: create the GitHub OIDC roles from your machine"
	@echo "make github-setup  One-time: set GitHub variables, secret and the dev environment (needs gh)"

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
