variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. stellenradar-dev."
}

variable "database_prefix" {
  type        = string
  default     = "stellenradar_"
  description = "Prefix for Glue database names. The docs use the short layer names (raw, curated, …)."
}

variable "data_bucket" {
  type        = string
  description = "Name of the data bucket holding all lakehouse layers."
}

variable "athena_results_bucket" {
  type        = string
  description = "Name of the bucket for Athena query results."
}

variable "bytes_scanned_cutoff_per_query" {
  type        = number
  default     = 1073741824 # 1 GiB – the whole dataset is far smaller; this only stops runaway queries
  description = "Athena aborts any query in the workgroup that scans more than this."
}

variable "force_destroy" {
  type    = bool
  default = false
}
