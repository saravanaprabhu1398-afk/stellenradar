variable "name" {
  type        = string
  description = "Function name, e.g. stellenradar-dev-ingest."
}

variable "description" {
  type    = string
  default = ""
}

variable "handler" {
  type        = string
  description = "Python handler path relative to src/, e.g. ingest.handler.handler."
}

variable "source_root" {
  type        = string
  description = "Path to the repository's src/ directory."
}

variable "packages" {
  type        = list(string)
  description = "Top-level packages under src/ to include in the zip, e.g. [\"common\", \"ingest\"]."
}

variable "memory_mb" {
  type    = number
  default = 256
}

variable "timeout_s" {
  type    = number
  default = 60
}

variable "environment" {
  type    = map(string)
  default = {}
}

variable "policy_json" {
  type        = string
  description = "Least-privilege IAM policy for the function's own work (logging permissions are added automatically)."
}

variable "log_retention_days" {
  type    = number
  default = 30
}
