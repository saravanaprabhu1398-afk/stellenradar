locals {
  name_prefix = "stellenradar-${var.env}"
}

module "monitoring" {
  source            = "../../modules/monitoring"
  name_prefix       = local.name_prefix
  alert_email       = var.alert_email
  monthly_limit_usd = var.monthly_budget_usd
}
data "aws_caller_identity" "current" {}

# The account already has a GitHub OIDC provider that other workloads may use, so it is looked up, not managed.
module "github_oidc" {
  source               = "../../modules/github_oidc"
  name_prefix          = local.name_prefix
  github_repo          = var.github_repo
  github_owner_id      = var.github_owner_id
  github_repo_id       = var.github_repo_id
  state_bucket         = "stellenradar-tfstate-${data.aws_caller_identity.current.account_id}"
  create_oidc_provider = false
}

module "storage" {
  source        = "../../modules/storage"
  name_prefix   = local.name_prefix
  account_id    = data.aws_caller_identity.current.account_id
  force_destroy = true # dev only – terraform destroy must be able to remove everything (NFR-05)
}

module "catalog" {
  source                = "../../modules/catalog"
  name_prefix           = local.name_prefix
  data_bucket           = module.storage.bucket_names["data"]
  athena_results_bucket = module.storage.bucket_names["athena-results"]
  force_destroy         = true
}

# ---------- Ingest Lambda (SDD §4.1) ----------

data "aws_iam_policy_document" "ingest" {
  statement {
    sid     = "WriteRaw"
    actions = ["s3:PutObject"]
    resources = [
      "${module.storage.bucket_arns["data"]}/raw/postings/*",
      "${module.storage.bucket_arns["data"]}/raw/manifests/*",
      "${module.storage.bucket_arns["data"]}/raw/state/*",
    ]
  }

  statement {
    sid       = "ReadListIndex"
    actions   = ["s3:GetObject"]
    resources = ["${module.storage.bucket_arns["data"]}/raw/state/*"]
  }

  # S3 answers GetObject on a missing key with NoSuchKey only if the caller may list the bucket; otherwise it is
  # AccessDenied and the first run (no list index yet) would fail. An s3:prefix condition does not help here:
  # GetObject requests carry no prefix key, so the condition would never match. Listing reveals key names only.
  statement {
    sid       = "ListDataBucket"
    actions   = ["s3:ListBucket"]
    resources = [module.storage.bucket_arns["data"]]
  }
}

module "ingest" {
  source      = "../../modules/lambda_function"
  name        = "${local.name_prefix}-ingest"
  description = "BA job search -> raw S3 (SDD 4.1)"
  handler     = "ingest.handler.handler"
  source_root = "${path.root}/../../../src"
  packages    = ["common", "ingest"]
  memory_mb   = 512
  timeout_s   = 900
  policy_json = data.aws_iam_policy_document.ingest.json

  environment = {
    DATA_BUCKET = module.storage.bucket_names["data"]
    MAX_DETAILS = "500"
    MAX_PAGES   = "50"
  }
}
