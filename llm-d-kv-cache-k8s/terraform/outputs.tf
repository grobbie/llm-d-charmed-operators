output "application" {
  value = juju_application.llm_d_kv_cache_k8s
}

output "provides" {
  value = {
    kv_cache_manager  = "kv-cache-manager"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    logging = "logging"
    tracing = "tracing"
  }
}
