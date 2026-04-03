#!/usr/bin/env python3
# Copyright 2026 Rob Gibbon
# See LICENSE file for licensing details.

import logging

import ops

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

SERVICE_NAME = "llm-d-prefill"


class LlmdPrefillK8sCharm(ops.CharmBase):
    """Juju Charm for managing the LLMD Prefill Worker.
    
    This charm orchestrates the vLLM engine optimized for compute-bound prompt processing.
    It passes the resulting KV caches off to decode workers via the Nixl connector, and explicitly
    publishes token metadata state to the KV Cache Manager over ZMQ for global locality routing.
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
        self.container = self.unit.get_container("llm-d-prefill")
        self.framework.observe(self.on.llm_d_prefill_pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.decode_worker_relation_changed, self._on_decode_relation_changed
        )
        self.framework.observe(
            self.on.kv_cache_manager_relation_changed, self._on_decode_relation_changed
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

    def _on_decode_relation_changed(self, event):
        # When decode worker endpoints change, we might want to restart or update config
        self._update_layer(event)

    def _update_layer(self, event):
        """Constructs the Pebble execution layer, verifies GPU relations, and applies Kubernetes StatefulSet patches."""
        cfg = self.charm_config
        if not cfg:
            self.unit.status = ops.BlockedStatus("Invalid configuration")
            return
            
        if not self.container.can_connect():
            self.unit.status = ops.WaitingStatus("Waiting for pebble")
            event.defer()
            return

        model_id = cfg.model_id
        if not model_id:
            self.unit.status = ops.BlockedStatus("Config 'model-id' is required")
            return

        # Check if decode-worker relation is ready
        decode_relation = self.model.get_relation("decode-worker")
        decode_endpoints = []
        if decode_relation:
            for unit in decode_relation.units:
                endpoint = decode_relation.data[unit].get("endpoint")
                if endpoint:
                    decode_endpoints.append(endpoint)

        kv_cache_endpoints = []
        kv_relation = self.model.get_relation("kv-cache-manager")
        if kv_relation:
            for unit in kv_relation.units:
                endpoint = kv_relation.data[unit].get("endpoint")
                if endpoint:
                    kv_cache_endpoints.append(endpoint)

        if not kv_cache_endpoints:
            self.unit.status = ops.BlockedStatus("Missing kv-cache-manager relation/endpoint")
            return
            
        if not decode_endpoints:
            self.unit.status = ops.BlockedStatus("Missing decode-worker relation/endpoint")
            return

        # Enforce CUDA specifically by checking the PyTorch wheel
        try:
            process = self.container.exec(["bash", "-c", "python3 -c 'import torch; print(torch.version.cuda)'"])
            stdout, _ = process.wait_output()
            if "None" in stdout or not stdout.strip():
                self.unit.status = ops.BlockedStatus("Disaggregation strictly requires a CUDA-enabled OCI image")
                return
        except Exception:
            self.unit.status = ops.BlockedStatus("Failed to verify PyTorch CUDA support in the container")
            return

        port = cfg.port
        
        # Compile Custom VLLM Execution string
        kv_transfer = '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'
        args = f"--port {port} --kv-transfer-config '{kv_transfer}'"
        
        # Inject Tensor Parallelism mathematically tied to K8s GPU requests
        gpu_count = cfg.gpu_count
        if gpu_count > 1:
            args += f" --tensor-parallel-size {gpu_count}"
        
        # Inject raw Engine tuning limits (--gpu-memory-utilization, --max-model-len, etc.)
        extra_args = cfg.extra_args
        if extra_args:
            args += f" {extra_args}"
            
        # Optional: Intercept Tempo OTEL endpoints
        if self.model.relations.get("tracing"):
            if self.tracing.is_ready():
                otlp_endpoint = self.tracing.get_endpoint("otlp_grpc")
                if otlp_endpoint:
                    args += f" --otlp-traces-endpoint {otlp_endpoint}"

        zmq_host = kv_cache_endpoints[0].split("//")[-1].split(":")[0]
        
        # Resolve the true Pod IP dynamically to prevent publisher collisions inside the centralized KV Cache
        import socket
        try:
            pod_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pod_ip = "127.0.0.1"
            
        topic = f"kv@{pod_ip}@{model_id}"
        kv_events = f'{{"enable_kv_cache_events":true,"publisher":"zmq","endpoint":"tcp://{zmq_host}:5557","topic":"{topic}"}}'
        args += f" --kv-events-config '{kv_events}'"

        if cfg.enable_chunked_prefill:
            args += " --enable-chunked-prefill"

        # Build dynamic bash runner securely decoupling arguments from generic Pebble YAML parsers
        script = f"#!/bin/bash\nexec vllm serve '{model_id}' {args}\n"
        self.container.push("/opt/launch_vllm.sh", script, permissions=0o755, make_dirs=True, user="2000", group="0")
        command = "/opt/launch_vllm.sh"

        # Map durable PVC mount paths alongside high-speed ephemeral emptyDirs
        env = {"HF_HOME": "/models", "VLLM_NIXL_SIDE_CHANNEL_HOST": pod_ip, "TRITON_CACHE_DIR": "/models/triton-cache", "TORCH_COMPILE_CACHE_DIR": "/models/torch-cache"}
        hf_token = cfg.hf_token
        if hf_token:
            env["HUGGING_FACE_HUB_TOKEN"] = hf_token

        # Expose InfiniBand / RDMA devices to NCCL explicitly if toggled
        if cfg.enable_infiniband:
            env["NCCL_IB_DISABLE"] = "0"
            env["NCCL_DEBUG"] = "INFO"
        else:
            env["NCCL_IB_DISABLE"] = "1"

        layer: ops.pebble.LayerDict = {
            "services": {
                SERVICE_NAME: {
                    "override": "replace",
                    "summary": "LLMD prefill worker",
                    "command": command,
                    "startup": "enabled",
                    "environment": env,
                    "user": "2000",
                    "group": "0",
                }
            },
            "checks": {
                "vllm-ready": {
                    "override": "replace",
                    "level": "ready",
                    "http": {"url": f"http://localhost:{port}/v1/models"},
                    "period": "10s",
                    "timeout": "5s",
                    "threshold": 3,
                },
                "vllm-live": {
                    "override": "replace",
                    "level": "alive",
                    "http": {"url": f"http://localhost:{port}/health"},
                    "period": "15s",
                    "timeout": "5s",
                    "threshold": 3,
                }
            }
        }

        self.container.add_layer("llm-d-prefill", layer, combine=True)
        self.container.replan()


        # Expose our own endpoint to the router
        prefill_relation = self.model.get_relation("prefill-worker")
        if prefill_relation:
            addr = self.model.get_binding(prefill_relation).network.bind_address
            prefill_relation.data[self.unit]["endpoint"] = f"http://{addr}:{port}"

        self.unit.status = ops.ActiveStatus()

if __name__ == "__main__":  # pragma: nocover
    ops.main(LlmdPrefillK8sCharm)
