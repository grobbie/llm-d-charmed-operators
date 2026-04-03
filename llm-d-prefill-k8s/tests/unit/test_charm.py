import ops
import pytest
from ops.testing import Harness
from unittest.mock import patch, MagicMock

from charm import LlmdPrefillK8sCharm

@pytest.fixture
def harness():
    h = Harness(LlmdPrefillK8sCharm)
    h.begin()
    yield h
    h.cleanup()

def test_missing_model_id(harness):
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({"model-id": ""})
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    assert "Config" in harness.model.unit.status.message

@patch("charm.Client")
def test_pebble_ready_no_kv_nodes(mock_client, harness):
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({"model-id": "Qwen/Qwen2-7B"})
    
    # Missing relation endpoints should block
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)

@patch("charm.TracingEndpointRequirer.get_endpoint")
@patch("charm.TracingEndpointRequirer.is_ready")
@patch("charm.Client")
def test_full_cluster_topology(mock_client, mock_is_ready, mock_get_endpoint, harness):
    mock_is_ready.return_value = True
    mock_get_endpoint.return_value = "http://tempo:4317"
    
    # Mock lightkube
    mock_mc = MagicMock()
    mock_client.return_value = mock_mc
    
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({
        "model-id": "Qwen/Qwen2-7B",
        "gpu-count": 2,
        "extra-args": "--dtype bfloat16"
    })
    
    # Setup relations
    rel_id = harness.add_relation("kv-cache-manager", "kv-manager")
    harness.add_relation_unit(rel_id, "kv-manager/0")
    harness.update_relation_data(rel_id, "kv-manager/0", {"endpoint": "tcp://10.1.1.1:5557"})
    
    decode_rel_id = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(decode_rel_id, "decode/0")
    harness.update_relation_data(decode_rel_id, "decode/0", {"endpoint": "tcp://decode:80"})
    
    # Establish a tracing relation natively so `self.model.relations.get('tracing')` succeeds.
    # The actual network payload is bypassed via the `@patch` decorator overrides.
    harness.add_relation("tracing", "tempo")
    
    # Mock container execution
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("12.1", "")
    with patch("ops.model.Container.exec", return_value=mock_process):
        # Trigger an update config to rerun the layer compilation now that all components are met
        harness.update_config({"port": 8080})
    
    # Check pebble plan
    plan = harness.get_container_pebble_plan("llm-d-prefill")
    assert plan.services["llm-d-prefill"] is not None
    assert plan.services["llm-d-prefill"].command == "/opt/launch_vllm.sh"
    
    script_content = harness.model.unit.get_container("llm-d-prefill").pull("/opt/launch_vllm.sh").read()
    
    # Verify our flag injections
    assert "Qwen/Qwen2-7B" in script_content
    assert "--tensor-parallel-size 2" in script_content
    assert "--dtype bfloat16" in script_content
    
    # Assert OTEL tracing injected into the wrapper!
    assert "otlp-traces-endpoint" in script_content
    
    assert isinstance(harness.model.unit.status, ops.ActiveStatus)
    
@patch("charm.Client")
def test_blocked_cuda(mock_client, harness):
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({"model-id": "Qwen/Qwen2-7B"})
    
    rel_id = harness.add_relation("kv-cache-manager", "kv-manager")
    harness.add_relation_unit(rel_id, "kv-manager/0")
    harness.update_relation_data(rel_id, "kv-manager/0", {"endpoint": "tcp://10.1.1.1:5557"})
    
    decode_rel_id = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(decode_rel_id, "decode/0")
    harness.update_relation_data(decode_rel_id, "decode/0", {"endpoint": "tcp://decode:80"})
    
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("None", "")
    with patch("ops.model.Container.exec", return_value=mock_process):
        harness.update_config({"port": 8080})
        
    assert isinstance(harness.model.unit.status, ops.BlockedStatus)
    assert "CUDA" in harness.model.unit.status.message
    

def test_pebble_connection_failure(harness):
    harness.set_can_connect("llm-d-prefill", False)
    harness.update_config({})
    assert isinstance(harness.model.unit.status, ops.WaitingStatus)

@patch("ops.model.Container.push")
def test_pebble_error_writing_config(mock_push, harness):
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({"model-id": "Qwen/Qwen2-7B"})
    
    rel_id = harness.add_relation("kv-cache-manager", "kv-manager")
    harness.add_relation_unit(rel_id, "kv-manager/0")
    harness.update_relation_data(rel_id, "kv-manager/0", {"endpoint": "tcp://10.1.1.1:5557"})
    
    decode_rel_id = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(decode_rel_id, "decode/0")
    harness.update_relation_data(decode_rel_id, "decode/0", {"endpoint": "tcp://decode:80"})
    
    mock_push.side_effect = ops.pebble.PathError("generic", "failed")
    
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("12.1", "")
    with patch("ops.model.Container.exec", return_value=mock_process):
        with pytest.raises(ops.pebble.PathError):
            harness.update_config({"port": 8080})

@patch("ops.model.Container.add_layer")
def test_pebble_api_error(mock_add_layer, harness):
    harness.set_can_connect("llm-d-prefill", True)
    harness.update_config({"model-id": "Qwen/Qwen2-7B"})
    
    rel_id = harness.add_relation("kv-cache-manager", "kv-manager")
    harness.add_relation_unit(rel_id, "kv-manager/0")
    harness.update_relation_data(rel_id, "kv-manager/0", {"endpoint": "tcp://10.1.1.1:5557"})
    
    decode_rel_id = harness.add_relation("decode-worker", "decode")
    harness.add_relation_unit(decode_rel_id, "decode/0")
    harness.update_relation_data(decode_rel_id, "decode/0", {"endpoint": "tcp://decode:80"})
    
    mock_add_layer.side_effect = ops.pebble.APIError(body={}, code=500, status="Error", message="api error")
    
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("12.1", "")
    with patch("ops.model.Container.exec", return_value=mock_process):
        with pytest.raises(ops.pebble.APIError):
            harness.update_config({"port": 8080})
