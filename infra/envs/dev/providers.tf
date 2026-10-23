provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project    = "stellenradar"
      env        = var.env
      managed_by = "terraform"
      repo       = "saravanaprabhu1398-afk/stellenradar"
    }
  }
}