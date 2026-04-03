import ops
import pytest
import yaml
from ops.testing import Harness
from unittest.mock import patch, MagicMock

from charm import LlmdInferenceSchedulerK8sCharm

@pytest.fixture
def harness():
    h = Harness(LlmdInferenceSchedulerK8sCharm)
    h.begin()
    yield h
    h.cleanup()

def test_missing_relations(harness):
    harness.set_can_connect("llm-d-inference-scheduler", True)
    harness.update_config({})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    assert "missing" in harness.model.unit.status.message.lower()

@patch("charm.TracingEndpointRequirer.get_endpoint")
@patch("charm.TracingEndpointRequirer.is_ready")
def test_pebble_ready_and_relations(mock_is_ready, mock_get_endpoint, harness):
    mock_is_ready.return_value = True
    mock_get_endpoint.return_value = "http://tempo:4317"
    
    harness.set_can_connect("llm-d-inference-scheduler", True)
    harness.set_can_connect("routing-sidecar", True)
    
    # Establish prefill topology
    rel_prefill = harness.add_relation("prefill-worker", "prefill")
    harness.add_relation_unit(rel_prefill, "prefill/0")
    harness.update_relation_data(rel_prefill, "prefill/0", {"endpoint": "prefill-0:8000"})
    
    # Establish decode topology
    rel_decode = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(rel_decode, "decode/0")
    harness.update_relation_data(rel_decode, "decode/0", {"endpoint": "decode-0:8000"})
    
    # Establish kv cache topology
    rel_kv = harness.add_relation("kv-cache-manager", "kv")
    harness.add_relation_unit(rel_kv, "kv/0")
    harness.update_relation_data(rel_kv, "kv/0", {"endpoint": "kv-0:8000"})
    
    harness.add_relation("tracing", "tempo")
    
    harness.update_config({
        "kv-cache-usage-metric": "vllm:kv_cache_usage_perc",
        "log-verbosity": 4
    })
    
    plan = harness.get_container_pebble_plan("llm-d-inference-scheduler")
    assert plan.services["llm-d-inference-scheduler"] is not None
    cmd = plan.services["llm-d-inference-scheduler"].command
    assert "--kv-cache-usage-percentage-metric=vllm:kv_cache_usage_perc" in cmd
    assert "-v=4" in cmd
    
    envoy_plan = harness.get_container_pebble_plan("routing-sidecar")
    assert envoy_plan.services["routing-sidecar"] is not None
    assert "envoy -c /etc/envoy/envoy.yaml" in envoy_plan.services["routing-sidecar"].command
    
    script_content = harness.model.unit.get_container("routing-sidecar").pull("/etc/envoy/envoy.yaml").read()
    assert "envoy.tracers.opentelemetry" in script_content
    
    assert isinstance(harness.model.unit.status, ops.ActiveStatus)


def test_pebble_connection_failure(harness):
    harness.set_can_connect("llm-d-inference-scheduler", False)
    harness.update_config({})
    assert isinstance(harness.model.unit.status, ops.WaitingStatus)

def test_relation_broken(harness):
    harness.set_can_connect("llm-d-inference-scheduler", True)
    harness.set_can_connect("routing-sidecar", True)
    
    rel_prefill = harness.add_relation("prefill-worker", "prefill")
    harness.add_relation_unit(rel_prefill, "prefill/0")
    harness.update_relation_data(rel_prefill, "prefill/0", {"endpoint": "prefill-0:8000"})
    
    harness.remove_relation(rel_prefill)
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)

@patch("ops.model.Container.push")
def test_pebble_error_writing_config(mock_push, harness):
    harness.set_can_connect("llm-d-inference-scheduler", True)
    harness.set_can_connect("routing-sidecar", True)
    
    rel_prefill = harness.add_relation("prefill-worker", "prefill")
    harness.add_relation_unit(rel_prefill, "prefill/0")
    harness.update_relation_data(rel_prefill, "prefill/0", {"endpoint": "prefill-0:8000"})
    
    rel_decode = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(rel_decode, "decode/0")
    harness.update_relation_data(rel_decode, "decode/0", {"endpoint": "decode-0:8000"})
    
    rel_kv = harness.add_relation("kv-cache-manager", "kv")
    harness.add_relation_unit(rel_kv, "kv/0")
    harness.update_relation_data(rel_kv, "kv/0", {"endpoint": "kv-0:8000"})
    
    mock_push.side_effect = ops.pebble.PathError("generic", "failed to write")
    
    with pytest.raises(ops.pebble.PathError):
        harness.update_config({"log-verbosity": 2})

@patch("ops.model.Container.add_layer")
def test_pebble_api_error(mock_add_layer, harness):
    harness.set_can_connect("llm-d-inference-scheduler", True)
    harness.set_can_connect("routing-sidecar", True)
    
    rel_prefill = harness.add_relation("prefill-worker", "prefill")
    harness.add_relation_unit(rel_prefill, "prefill/0")
    harness.update_relation_data(rel_prefill, "prefill/0", {"endpoint": "prefill-0:8000"})
    
    rel_decode = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(rel_decode, "decode/0")
    harness.update_relation_data(rel_decode, "decode/0", {"endpoint": "decode-0:8000"})
    
    rel_kv = harness.add_relation("kv-cache-manager", "kv")
    harness.add_relation_unit(rel_kv, "kv/0")
    harness.update_relation_data(rel_kv, "kv/0", {"endpoint": "kv-0:8000"})
    
    mock_add_layer.side_effect = ops.pebble.APIError(body={}, code=500, status="Internal Server Error", message="api fail")
    
    with pytest.raises(ops.pebble.APIError):
        harness.update_config({"log-verbosity": 2})
