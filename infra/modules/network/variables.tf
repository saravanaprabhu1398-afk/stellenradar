variable "name_prefix" {
  type = string
}

variable "cidr_block" {
  type    = string
  default = "10.20.0.0/16"
}

variable "private_subnets" {
  type        = map(string)
  default     = { a = "10.20.1.0/24", b = "10.20.2.0/24" }
  description = "Availability-zone suffix -> CIDR. RDS needs subnets in at least two AZs."
}
