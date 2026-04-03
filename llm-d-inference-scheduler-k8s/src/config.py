# Copyright 2026 Rob Gibbon
# See LICENSE file for licensing details.

from pydantic import BaseModel, Field

class CharmConfig(BaseModel):
    """Pydantic model for validating charm configuration."""
    port: int = Field(default=8000, gt=0, lt=65536)
    kv_cache_usage_metric: str = Field(alias="kv-cache-usage-metric", default="vllm:kv_cache_usage_perc")
    log_verbosity: int = Field(alias="log-verbosity", default=0)
    prometheus_url: str = Field(alias="prometheus-url", default="http://prometheus-k8s-0.prometheus-k8s-endpoints.cos.svc.cluster.local:9090")
    metrics_interval: str = Field(alias="metrics-interval", default="10s")
    scoring_plugins: str = Field(alias="scoring-plugins", default="queue-scorer,kv-cache-utilization-scorer")
    router_port: int = Field(alias="router-port", default=8080, gt=0, lt=65536)
    admin_port: int = Field(alias="admin-port", default=9901, gt=0, lt=65536)

    model_config = {"populate_by_name": True, "extra": "ignore"}
