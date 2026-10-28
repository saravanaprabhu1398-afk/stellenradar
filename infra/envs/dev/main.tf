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

# ---------- Curate Lambda: SCD2 merge in Athena (SDD §4.2) ----------

data "aws_region" "current" {}

locals {
  glue_prefix = "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
  athena_wg   = "arn:aws:athena:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:workgroup/${module.catalog.athena_workgroup}"
}

data "aws_iam_policy_document" "curate" {
  statement {
    sid       = "RunQueries"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"]
    resources = [local.athena_wg]
  }

  statement {
    sid = "Catalog"
    actions = [
      "glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables",
      "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition",
      "glue:CreateTable", "glue:UpdateTable",
    ]
    resources = [
      "${local.glue_prefix}:catalog",
      "${local.glue_prefix}:database/${module.catalog.database_names["raw"]}",
      "${local.glue_prefix}:database/${module.catalog.database_names["curated"]}",
      "${local.glue_prefix}:table/${module.catalog.database_names["raw"]}/*",
      "${local.glue_prefix}:table/${module.catalog.database_names["curated"]}/*",
    ]
  }

  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [module.storage.bucket_arns["data"], module.storage.bucket_arns["athena-results"]]
  }

  statement {
    sid       = "ReadRaw"
    actions   = ["s3:GetObject"]
    resources = ["${module.storage.bucket_arns["data"]}/raw/*"]
  }

  statement {
    sid       = "WriteCurated"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${module.storage.bucket_arns["data"]}/curated/*"]
  }

  statement {
    sid       = "QueryResults"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${module.storage.bucket_arns["athena-results"]}/*"]
  }
}

module "curate" {
  source      = "../../modules/lambda_function"
  name        = "${local.name_prefix}-curate"
  description = "SCD2 merge of one ingest run into curated.postings (SDD 4.2)"
  handler     = "curate.handler.handler"
  source_root = "${path.root}/../../../src"
  packages    = ["common", "curate"]
  memory_mb   = 256
  timeout_s   = 600
  policy_json = data.aws_iam_policy_document.curate.json

  environment = {
    DATA_BUCKET      = module.storage.bucket_names["data"]
    ATHENA_WORKGROUP = module.catalog.athena_workgroup
  }
}

# ---------- Enrich Lambda: field extraction with Bedrock (SDD §4.3, ADR-005) ----------

locals {
  extract_model = "amazon.nova-lite-v1:0"
}

data "aws_iam_policy_document" "enrich" {
  # The EU inference profile routes each call to a foundation model in one of the EU regions, so both the profile
  # and the model in every region it may route to must be allowed.
  statement {
    sid     = "InvokeExtractionModel"
    actions = ["bedrock:InvokeModel"]
    resources = [
      "arn:aws:bedrock:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:inference-profile/eu.${local.extract_model}",
      "arn:aws:bedrock:*::foundation-model/${local.extract_model}",
    ]
  }

  statement {
    sid       = "RunQueries"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"]
    resources = [local.athena_wg]
  }

  statement {
    sid     = "Catalog"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:UpdateTable"]
    resources = [
      "${local.glue_prefix}:catalog",
      "${local.glue_prefix}:database/${module.catalog.database_names["curated"]}",
      "${local.glue_prefix}:table/${module.catalog.database_names["curated"]}/*",
    ]
  }

  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [module.storage.bucket_arns["data"], module.storage.bucket_arns["athena-results"]]
  }

  statement {
    sid       = "ReadCurated"
    actions   = ["s3:GetObject"]
    resources = ["${module.storage.bucket_arns["data"]}/curated/*"]
  }

  statement {
    sid       = "WriteEnrichment"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${module.storage.bucket_arns["data"]}/curated/posting_enrichment/*"]
  }

  statement {
    sid     = "WriteStaging"
    actions = ["s3:PutObject"]
    resources = [
      "${module.storage.bucket_arns["data"]}/staging/enrichment/*",
      "${module.storage.bucket_arns["data"]}/staging/dead_letter/*",
    ]
  }

  statement {
    sid       = "QueryResults"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${module.storage.bucket_arns["athena-results"]}/*"]
  }
}

module "enrich" {
  source      = "../../modules/lambda_function"
  name        = "${local.name_prefix}-enrich"
  description = "Structured field extraction for current postings (SDD 4.3)"
  handler     = "enrich.handler.handler"
  source_root = "${path.root}/../../../src"
  packages    = ["common", "curate", "enrich"]
  memory_mb   = 512
  timeout_s   = 900
  policy_json = data.aws_iam_policy_document.enrich.json

  environment = {
    DATA_BUCKET        = module.storage.bucket_names["data"]
    ATHENA_WORKGROUP   = module.catalog.athena_workgroup
    EXTRACT_MODEL_ID   = "eu.${local.extract_model}"
    PROMPT_VERSION     = "v1"
    MAX_ENRICH_PER_RUN = "200"
  }
}

# ---------- Daily pipeline: schedule -> Step Functions -> ingest -> curate -> enrich (SDD §4.10) ----------

module "orchestration" {
  source              = "../../modules/orchestration"
  name_prefix         = local.name_prefix
  alert_email         = var.alert_email
  ingest_function_arn = module.ingest.function_arn
  curate_function_arn = module.curate.function_arn
  enrich_function_arn = module.enrich.function_arn
}
