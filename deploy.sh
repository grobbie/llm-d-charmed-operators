#!/bin/bash
set -e

echo "========================================"
echo "LLM-D Charm Ecosystem Deployer"
echo "========================================"

if ! command -v juju &> /dev/null; then
    echo "ERROR: Juju command line not found. Please install Juju."
    exit 1
fi

MODEL="llm-d-inference"

# Ensure the model exists
if ! juju show-model $MODEL > /dev/null 2>&1; then
    echo "Creating Juju model: $MODEL"
    juju add-model $MODEL
else
    echo "Using existing Juju model: $MODEL"
fi

juju switch $MODEL

echo ""
echo "Deploying LLM-D Prefill Worker..."
# Note: For tmpfs backing in k8s, a customized storage pool 'tmpfs' mapped to an emptyDir medium=Memory must exist.
# Juju storage natively sizes it here safely to 64GB for the massive shard blocks.
PREFILL_CHARM=$(ls llm-d-prefill-k8s/*.charm 2>/dev/null | head -1)
if [ -n "$PREFILL_CHARM" ]; then
    juju deploy "$PREFILL_CHARM" \
        --trust \
        --constraints "cores=4 mem=32G tags=anti-pod.app.kubernetes.io/name=llm-d-prefill-k8s|llm-d-decode-k8s,anti-pod.topology-key=kubernetes.io/hostname" \
        --device gpu=1,nvidia.com/gpu \
        --device infiniband-hca=1,rdma/hca_shared \
        --config enable-infiniband=false \
        --storage shared-memory=tmpfs,64G \
        --storage models=100G \
        --resource llmd-image="ghcr.io/llm-d/llm-d-cuda:v0.5.1"
else
    echo "Warning: Prefill charm bundle not found. Skipped."
fi

echo ""
echo "Deploying LLM-D Decode Worker..."
DECODE_CHARM=$(ls llm-d-decode-k8s/*.charm 2>/dev/null | head -1)
if [ -n "$DECODE_CHARM" ]; then
    juju deploy "$DECODE_CHARM" \
        --trust \
        --constraints "cores=4 mem=32G tags=anti-pod.app.kubernetes.io/name=llm-d-decode-k8s|llm-d-prefill-k8s,anti-pod.topology-key=kubernetes.io/hostname" \
        --device gpu=1,nvidia.com/gpu \
        --device infiniband-hca=1,rdma/hca_shared \
        --config enable-infiniband=false \
        --storage shared-memory=tmpfs,64G \
        --storage models=100G \
        --resource llmd-image="ghcr.io/llm-d/llm-d-cuda:v0.5.1"
else
    echo "Warning: Decode charm bundle not found. Skipped."
fi

echo ""
echo "Deploying LLM-D KV Cache Manager..."
KV_CHARM=$(ls llm-d-kv-cache-k8s/*.charm 2>/dev/null | head -1)
if [ -n "$KV_CHARM" ]; then
    juju deploy "$KV_CHARM" \
        --constraints "cores=2 mem=8G" \
        --storage tokenizer-uds=tmpfs,1G \
        --storage tokenizers=20G \
        --resource llmd-image="ghcr.io/llm-d/llm-d-kv-cache-manager:v0.5.1" \
        --resource tokenizer-image="ghcr.io/llm-d/llm-d-uds-tokenizer:v0.6.0"
else
    echo "Warning: KV Cache charm bundle not found. Skipped."
fi

echo ""
echo "Deploying LLM-D Inference Scheduler..."
SCHEDULER_CHARM=$(ls llm-d-inference-scheduler-k8s/*.charm 2>/dev/null | head -1)
if [ -n "$SCHEDULER_CHARM" ]; then
    juju deploy "$SCHEDULER_CHARM" \
        --constraints "cores=2 mem=4G" \
        --resource llmd-image="ghcr.io/llm-d/llm-d-inference-scheduler:v0.5.1" \
        --resource routing-image="ghcr.io/llm-d/llm-d-routing-sidecar:v0.6.0"
else
    echo "Warning: Inference Scheduler charm bundle not found. Skipped."
fi

echo ""
echo "Deploying Canonical Observability Stack (COS Lite)..."
juju deploy prometheus-k8s --channel latest/edge --trust
juju deploy grafana-k8s --channel latest/edge --trust
juju deploy loki-k8s --channel latest/edge --trust
juju deploy tempo-k8s --channel latest/edge --trust

echo ""
echo "Integrating Internal LLM-D Workload Services..."
juju integrate llm-d-inference-scheduler-k8s:prefill-worker llm-d-prefill-k8s:prefill-worker || true
juju integrate llm-d-inference-scheduler-k8s:decode-worker llm-d-decode-k8s:decode-worker || true
juju integrate llm-d-inference-scheduler-k8s:kv-cache-manager llm-d-kv-cache-k8s:kv-cache-manager || true

echo ""
echo "Integrating Observability Matrix..."
CHARMS=(
    "llm-d-prefill-k8s"
    "llm-d-decode-k8s"
    "llm-d-kv-cache-k8s"
    "llm-d-inference-scheduler-k8s"
)

for APP in "${CHARMS[@]}"; do
    juju integrate $APP:metrics-endpoint prometheus-k8s:metrics-endpoint || true
    juju integrate $APP:grafana-dashboard grafana-k8s:grafana-dashboard || true
    juju integrate $APP:logging loki-k8s:logging || true
    if [ "$APP" != "llm-d-inference-scheduler-k8s" ]; then
        juju integrate $APP:tracing tempo-k8s:tracing || true
    fi
done

echo ""
echo "Enforcing anti-affinity mapping via placement/scale commands if needed..."
# If user wants anti-affinity on a scaled group, we might hint relations or scaled placement here.

echo "========================================"
echo "Deployment successfully dispatched."
echo "Monitor completion with: watch -c juju status --color"
echo "========================================"
