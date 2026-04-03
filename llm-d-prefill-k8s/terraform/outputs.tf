output "application" {
  value = juju_application.llm_d_prefill_k8s
}

output "provides" {
  value = {
    prefill_worker    = "prefill-worker"
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    decode_worker    = "decode-worker"
    kv_cache_manager = "kv-cache-manager"
    logging          = "logging"
    tracing          = "tracing"
  }
}
