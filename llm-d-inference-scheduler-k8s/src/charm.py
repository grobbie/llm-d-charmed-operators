#!/usr/bin/env python3
# Copyright 2026 Rob Gibbon
# See LICENSE file for licensing details.

import logging

import ops
import jinja2

from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from lightkube import Client
from lightkube.resources.apps_v1 import StatefulSet
from lightkube.models.core_v1 import ResourceRequirements
from pydantic import ValidationError
from config import CharmConfig
import yaml

logger = logging.getLogger(__name__)

SERVICE_NAME = "llm-d-inference-scheduler"


class LlmdInferenceSchedulerK8sCharm(ops.CharmBase):
    """Juju Charm for managing the LLMD Inference Scheduler with Envoy proxy integrations.
    
    This charm natively orchestrates the ExtProc Envoy gRPC architecture, delegating token
    route decisions exclusively based on precise queue-scoring and ZMQ token metrics logic.
    """
    @property
    def charm_config(self) -> CharmConfig | None:
        try:
            return CharmConfig(**self.model.config)
        except ValidationError as e:
            logger.error(f"Config validation error: {e}")
            return None

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container("llm-d-inference-scheduler")
        self.framework.observe(self.on.llm_d_inference_scheduler_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.framework.observe(self.on.prefill_worker_relation_changed, self._on_relation_changed)
        self.framework.observe(self.on.decode_worker_relation_changed, self._on_relation_changed)
        self.framework.observe(self.on.kv_cache_manager_relation_changed, self._on_relation_changed)

        # Observability
        cfg = self.charm_config
        port = cfg.port if cfg else 8000
        self.metrics_endpoint = MetricsEndpointProvider(self, jobs=[
            {
                "static_configs": [{"targets": [f"*:{port}"]}]
            },
            {
                "static_configs": [{"targets": ["*:9901"]}],
                "metrics_path": "/stats/prometheus"
            }
        ])
        self.loki_consumer = LokiPushApiConsumer(self)
        self.grafana_dashboard = GrafanaDashboardProvider(self)
        self.tracing = TracingEndpointRequirer(self, protocols=["otlp_grpc"])

        self.unit.set_ports(port)

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        self._update_layer(event)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        cfg = self.charm_config
        if not cfg:
            self.unit.status = ops.BlockedStatus("Invalid configuration")
            return
        self.unit.set_ports(cfg.port)
        self._update_layer(event)

    def _on_relation_changed(self, event):
        self._update_layer(event)

    def _update_layer(self, event):
        """Builds custom scoring configuration maps and generates dynamic Envoy cluster pipelines."""
        cfg = self.charm_config
        if not cfg:
            self.unit.status = ops.BlockedStatus("Invalid configuration")
            return
            
        if not self.container.can_connect():
            self.unit.status = ops.WaitingStatus("Waiting for pebble")
            event.defer()
            return

        # Fetch endpoints from relations
        prefill_endpoints = []
        prefill_rel = self.model.get_relation("prefill-worker")
        if prefill_rel:
            for unit in prefill_rel.units:
                endpoint = prefill_rel.data[unit].get("endpoint")
                if endpoint:
                    prefill_endpoints.append(endpoint)

        decode_endpoints = []
        decode_rel = self.model.get_relation("decode-worker")
        if decode_rel:
            for unit in decode_rel.units:
                endpoint = decode_rel.data[unit].get("endpoint")
                if endpoint:
                    decode_endpoints.append(endpoint)

        kv_cache_endpoints = []
        kv_rel = self.model.get_relation("kv-cache-manager")
        if kv_rel:
            for unit in kv_rel.units:
                endpoint = kv_rel.data[unit].get("endpoint")
                if endpoint:
                    kv_cache_endpoints.append(endpoint)

        if not prefill_endpoints:
            self.unit.status = ops.BlockedStatus("Missing prefill-worker relation/endpoint")
            return

        if not decode_endpoints:
            self.unit.status = ops.BlockedStatus("Missing decode-worker relation/endpoint")
            return

        port = cfg.port
        
        plugins = cfg.scoring_plugins.split(",")
        plugins = [p.strip() for p in plugins if p.strip()]

        config_data = {
            "plugins": [{"name": p} for p in plugins],
            "routing": {
                "prefill_endpoints": prefill_endpoints,
                "decode_endpoints": decode_endpoints
            }
        }
        
        prom_url = cfg.prometheus_url
        if prom_url:
            config_data["metrics"] = {
                "prometheus_url": prom_url,
                "interval": cfg.metrics_interval
            }

        self.container.push("/config/router-config.yaml", yaml.dump(config_data), make_dirs=True, user="65532", group="65532")

        command = f"/app/inference-scheduler --configPath=/config/router-config.yaml --extProcPort=9002 --metricsPort={port}"
        
        kv_cache_metric = cfg.kv_cache_usage_metric
        log_verbosity = cfg.log_verbosity
        command += f" --kv-cache-usage-percentage-metric={kv_cache_metric} -v={log_verbosity}"

        # Propagate tracing globally
        env = {}
        otlp_endpoint = None
        otlp_host = None
        otlp_port = 4317
        if self.model.relations.get("tracing"):
            if self.tracing.is_ready():
                otlp_endpoint = self.tracing.get_endpoint("otlp_grpc")
                if otlp_endpoint:
                    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint
                    env["OTEL_TRACES_EXPORTER"] = "otlp"
                    
                    import urllib.parse
                    parsed = urllib.parse.urlparse(otlp_endpoint)
                    otlp_host = parsed.hostname or otlp_endpoint.split(":")[1].strip("/")
                    otlp_port = parsed.port or 4317

        router_port = cfg.router_port
        admin_port = cfg.admin_port

        layer: ops.pebble.LayerDict = {
            "services": {
                SERVICE_NAME: {
                    "override": "replace",
                    "summary": "LLMD inference scheduler ExtProc worker",
                    "command": command,
                    "startup": "enabled",
                    "environment": env,
                    "user": "65532",
                    "group": "65532",
                }
            },
            "checks": {
                "scheduler-live": {
                    "override": "replace",
                    "level": "alive",
                    "tcp": {"port": 9002},
                    "period": "10s",
                    "timeout": "5s",
                    "threshold": 3,
                }
            }
        }

        self.container.add_layer("llm-d-inference-scheduler", layer, combine=True)
        self.container.replan()
        
        
        routing_container = self.unit.get_container("routing-sidecar")
        if routing_container.can_connect():
            envoy_tracing_filter = ""
            envoy_tracing_cluster = ""
            
            if otlp_host:
                envoy_tracing_filter = f"""
          tracing:
            provider:
              name: envoy.tracers.opentelemetry
              typed_config:
                "@type": type.googleapis.com/envoy.config.trace.v3.OpenTelemetryConfig
                grpc_service:
                  envoy_grpc:
                    cluster_name: opentelemetry_collector
                  timeout: 0.250s
                service_name: llm-d-inference-scheduler
"""
                envoy_tracing_cluster = f"""
  - name: opentelemetry_collector
    connect_timeout: 0.25s
    type: STRICT_DNS
    lb_policy: ROUND_ROBIN
    http2_protocol_options: {{}}
    load_assignment:
      cluster_name: opentelemetry_collector
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: {otlp_host}
                port_value: {otlp_port}
"""

            env = jinja2.Environment(loader=jinja2.FileSystemLoader("src/templates"))
            template = env.get_template("envoy.yaml.j2")
            envoy_config = template.render(
                router_port=router_port,
                admin_port=admin_port,
                envoy_tracing_filter=envoy_tracing_filter,
                envoy_tracing_cluster=envoy_tracing_cluster
            )
            routing_container.push("/etc/envoy/envoy.yaml", envoy_config.strip(), make_dirs=True, user="65532", group="65532")
            routing_layer: ops.pebble.LayerDict = {
                "services": {
                    "routing-sidecar": {
                        "override": "replace",
                        "summary": "LLMD Envoy Data Plane",
                        "command": "envoy -c /etc/envoy/envoy.yaml",
                        "startup": "enabled",
                        "user": "65532",
                        "group": "65532",
                    }
                },
                "checks": {
                    "envoy-ready": {
                        "override": "replace",
                        "level": "ready",
                        "tcp": {"port": admin_port},
                        "period": "10s",
                        "timeout": "5s",
                        "threshold": 3,
                    }
                }
            }
            routing_container.add_layer("routing-sidecar", routing_layer, combine=True)
            routing_container.replan()

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(LlmdInferenceSchedulerK8sCharm)
