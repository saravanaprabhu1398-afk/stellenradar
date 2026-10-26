variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. stellenradar-dev."
}

variable "alert_email" {
  type        = string
  description = "Recipient of pipeline failure alerts (must confirm the SNS subscription once)."
}

variable "ingest_function_arn" {
  type = string
}

variable "curate_function_arn" {
  type = string
}

variable "schedule_expression" {
  type        = string
  default     = "cron(0 6 * * ? *)"
  description = "When the daily run starts, in schedule_timezone."
}

variable "schedule_timezone" {
  type    = string
  default = "Europe/Berlin"
}

variable "schedule_enabled" {
  type    = bool
  default = true
}

variable "freshness_alarm_hours" {
  type        = number
  default     = 26
  description = "Alarm when no run has succeeded for this many hours (NFR-01)."
}
