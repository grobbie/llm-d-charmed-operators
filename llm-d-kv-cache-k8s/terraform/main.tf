terraform {
  required_providers {
    juju = {
      source  = "juju/juju"
      version = ">= 1.0.0"
    }
  }
}

resource "juju_application" "llm_d_kv_cache_k8s" {
  name       = var.app_name
  model      = var.model_name
  
  charm {
    name     = "llm-d-kv-cache-manager-k8s"
    revision = var.revision
    channel  = var.channel
  }

  constraints = var.constraints
  config      = var.config

  units = var.units
}
