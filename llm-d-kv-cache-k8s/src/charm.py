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
from lightkube.models.core_v1 import EmptyDirVolumeSource, Volume, VolumeMount, ResourceRequirements
from pydantic import ValidationError
from config import CharmConfig

logger = logging.getLogger(__name__)

SERVICE_NAME = "llm-d-kv-cache"

class LlmdKvCacheK8sCharm(ops.CharmBase):
    """Juju Charm for managing the LLMD KV Cache Manager.
    
    This charm deploys the centralized KV index registry. It embeds a natively bound
    ZeroMQ pub/sub mechanism to map live vLLM VRAM blocks, utilizing an embedded 
    socket-bound C++ Tokenizer sidecar to execute highly complex `precise-prefix-cache` 
    heuristics upon HTTP request arrays!
    """
    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container("llm-d-kv-cache")
        self.framework.observe(self.on.llm_d_kv_cache_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        
        self.framework.observe(
            self.on.kv_cache_manager_relation_joined, self._on_relation_joined
        )

        # Observability
        cfg = self.charm_config
        port = cfg.port if cfg else 8000
        self.metrics_endpoint = MetricsEndpointProvider(self, jobs=[
            {
                "static_configs": [
                    {
                        "targets": [f"*:{port}"]
                    }
                ]
            }
        ])
        self.loki_consumer = LokiPushApiConsumer(self)
        self.grafana_dashboard = GrafanaDashboardProvider(self)
        
        self.tracing = TracingEndpointRequirer(self, protocols=["otlp_grpc"])

        self.unit.set_ports(port, 5557)

    @property
    def charm_config(self) -> CharmConfig | None:
        try:
            return CharmConfig(**self.model.config)
        except ValidationError as e:
            logger.error(f"Config validation error: {e}")
            return None

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        self._update_layer(event)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        cfg = self.charm_config
        if not cfg:
            self.unit.status = ops.BlockedStatus("Invalid configuration")
            return
        self.unit.set_ports(cfg.port, 5557) # zero mq port for receiving token streams
        self._update_layer(event)

    def _on_relation_joined(self, event):
        """Saturates the Juju databus payload informing upstream ExtProc routers precisely where to query HTTP scores."""
        # Publish our endpoint so prefill or router can send requests
        cfg = self.charm_config
        port = cfg.port if cfg else 8000
        addr = self.model.get_binding(event.relation).network.bind_address
        event.relation.data[self.unit]["endpoint"] = f"http://{addr}:{port}"

    def _update_layer(self, event):
        """Assembles both the Go scheduler layer and the fast Unix Domain Socket Sidecar layer."""
        cfg = self.charm_config
        if not cfg:
            self.unit.status = ops.BlockedStatus("Invalid configuration")
            return
            
        if not self.container.can_connect():
            self.unit.status = ops.WaitingStatus("Waiting for pebble")
            event.defer()
            return

        port = cfg.port
        command = "/app/kv-cache-manager --configPath=/config/kv-cache-config.yaml"

        indexer_cache_size = cfg.indexer_cache_size
        env_j2 = jinja2.Environment(loader=jinja2.FileSystemLoader("src/templates"))
        kv_cache_template = env_j2.get_template("kv-cache-config.yaml.j2")
        kv_cache_config = kv_cache_template.render(
            indexer_cache_size=indexer_cache_size
        )
        self.container.push("/config/kv-cache-config.yaml", kv_cache_config, make_dirs=True, user="65532", group="65532")

        env = {
            "HTTP_PORT": str(port),
            "PYTHONPATH": "/app/pkg/preprocessing/chat_completions:/workspace/build/venv/lib/python3.12/site-packages",
            "TOKENIZERS_UDS_SOCKET": "/tmp/tokenizer/tokenizer-uds.socket"
        }
        
        if self.model.relations.get("tracing"):
            if self.tracing.is_ready():
                otlp_endpoint = self.tracing.get_endpoint("otlp_grpc")
                if otlp_endpoint:
                    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint
                    env["OTEL_TRACES_EXPORTER"] = "otlp"

        layer: ops.pebble.LayerDict = {
            "services": {
                SERVICE_NAME: {
                    "override": "replace",
                    "summary": "LLMD KV Cache Manager",
                    "command": command,
                    "startup": "enabled",
                    "environment": env,
                    "user": "65532",
                    "group": "65532",
                }
            },
            "checks": {
                "kv-cache-live": {
                    "override": "replace",
                    "level": "ready",
                    "tcp": {"port": port},
                    "period": "10s",
                    "timeout": "5s",
                    "threshold": 3,
                }
            }
        }

        self.container.add_layer("llm-d-kv-cache", layer, combine=True)
        self.container.replan()
        
        tokenizer_container = self.unit.get_container("uds-tokenizer")
        if tokenizer_container.can_connect():
            script = "#!/bin/bash\nif [ -f /app/tokenizer_server ]; then exec /app/tokenizer_server --configPath=/config/tokenizer-config.yaml; else exec /usr/local/bin/llm-d-uds-tokenizer --configPath=/config/tokenizer-config.yaml; fi\n"
            tokenizer_container.push("/opt/launch.sh", script, permissions=0o755, make_dirs=True, user="65532", group="65532")

            tokenizer_model_name = cfg.tokenizer_model_name
            tokenizer_workers_count = cfg.tokenizer_workers_count
            tokenizer_local = cfg.tokenizer_local
            
            tokenizer_template = env_j2.get_template("tokenizer-config.yaml.j2")
            tokenizer_config = tokenizer_template.render(
                tokenizer_model_name=tokenizer_model_name,
                tokenizer_workers_count=tokenizer_workers_count,
                tokenizer_local=str(tokenizer_local).lower()
            )
            tokenizer_container.push("/config/tokenizer-config.yaml", tokenizer_config, make_dirs=True, user="65532", group="65532")
            
            hf_token = cfg.hf_token
            tk_env = {
                "TOKENIZERS_DIR": "/tokenizers",
                "HF_HOME": "/tokenizers"
            }
            if hf_token:
                tk_env["HF_TOKEN"] = hf_token
                
            tk_layer: ops.pebble.LayerDict = {
                "services": {
                    "uds-tokenizer": {
                        "override": "replace",
                        "summary": "LLMD UDS Tokenizer Sidecar",
                        "command": "/opt/launch.sh",
                        "startup": "enabled",
                        "environment": tk_env,
                        "user": "65532",
                        "group": "65532",
                    }
                }
            }
            tokenizer_container.add_layer("uds-tokenizer", tk_layer, combine=True)
            tokenizer_container.replan()

        self.unit.status = ops.ActiveStatus()

if __name__ == "__main__":  # pragma: nocover
    ops.main(LlmdKvCacheK8sCharm)
