output "application" {
  value = juju_application.llm_d_inference_scheduler_k8s
}

output "provides" {
  value = {
    metrics_endpoint  = "metrics-endpoint"
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    prefill_worker   = "prefill-worker"
    decode_worker    = "decode-worker"
    kv_cache_manager = "kv-cache-manager"
    logging          = "logging"
    tracing          = "tracing"
  }
}
