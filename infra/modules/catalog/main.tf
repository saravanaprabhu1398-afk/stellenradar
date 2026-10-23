locals {
  layers = ["raw", "staging", "curated", "marts"]
}

# Access is controlled by IAM for now; Lake Formation permissions are added in Week 3 (security doc §6).
resource "aws_glue_catalog_database" "layer" {
  for_each     = toset(local.layers)
  name         = "${var.database_prefix}${each.key}"
  description  = "Stellenradar ${each.key} layer"
  location_uri = "s3://${var.data_bucket}/${each.key}/"
  tags         = { component = "catalog" }
}

resource "aws_athena_workgroup" "this" {
  name          = var.name_prefix
  description   = "Stellenradar pipeline, dbt and ad-hoc queries"
  force_destroy = var.force_destroy
  tags          = { component = "catalog" }

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location = "s3://${var.athena_results_bucket}/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
