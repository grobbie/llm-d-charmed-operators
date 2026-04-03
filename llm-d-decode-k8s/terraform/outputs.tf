output "application" {
  value = juju_application.llm_d_decode_k8s
}

output "provides" {
  value = {
    decode_worker     = "decode-worker"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    kv_cache_manager = "kv-cache-manager"
    logging          = "logging"
    tracing          = "tracing"
  }
}
