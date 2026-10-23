variable "name_prefix" {
  type        = string
  description = "Prefix for resource names, e.g. stellenradar-dev."
}

variable "alert_email" {
  type        = string
  description = "Email address for budget and anomaly alerts."
}

variable "monthly_limit_usd" {
  type        = number
  description = "Monthly cost budget in USD (PRD NFR-03)."
}

variable "actual_alert_thresholds_usd" {
  type        = list(number)
  default     = [10, 25, 50]
  description = "Alert when actual monthly cost exceeds these amounts."
}

variable "anomaly_threshold_usd" {
  type        = number
  default     = 5
  description = "Alert on cost anomalies with at least this total impact."
}

# The account's service-level anomaly monitor already existed (AWS default) and is imported, not created.
variable "anomaly_monitor_name" {
  type    = string
  default = "Default-Services-Monitor"
}

variable "anomaly_subscription_name" {
  type    = string
  default = "Default-Services-Subscription"
}