terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = ">= 1.0.0"
    }
  }
}

resource "juju_application" "llm_d_decode_k8s" {
  name       = var.app_name
  model      = var.model_name
  trust      = true

  charm {
    name     = "llm-d-decode-k8s"
    revision = var.revision
    channel  = var.channel
  }

  constraints = var.constraints
  devices     = var.devices
  config      = var.config

  units = var.units
}
