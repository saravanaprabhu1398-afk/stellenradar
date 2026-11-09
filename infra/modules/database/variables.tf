variable "name_prefix" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "engine_version" {
  type        = string
  default     = "16"
  description = "Major version; RDS picks the default minor and applies minor upgrades automatically."
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "stop_schedule" {
  type        = string
  default     = "cron(0 22 * * ? *)"
  description = "When the nightly safety-net stop runs (Europe/Berlin)."
}
