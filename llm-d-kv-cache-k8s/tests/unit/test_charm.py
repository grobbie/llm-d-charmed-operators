import ops
import pytest
from ops.testing import Harness
from unittest.mock import patch, MagicMock

from charm import LlmdKvCacheK8sCharm

@pytest.fixture
def harness():
    h = Harness(LlmdKvCacheK8sCharm)
    h.begin()
    yield h
    h.cleanup()

@patch("charm.TracingEndpointRequirer.get_endpoint")
@patch("charm.TracingEndpointRequirer.is_ready")
@patch("charm.Client")
def test_pebble_ready_and_config(mock_client, mock_is_ready, mock_get_endpoint, harness):
    mock_is_ready.return_value = True
    mock_get_endpoint.return_value = "http://tempo:4317"
    
    mock_mc = MagicMock()
    mock_client.return_value = mock_mc
    mock_statefulset = MagicMock()
    mock_mc.get.return_value = mock_statefulset
    
    harness.set_can_connect("llm-d-kv-cache", True)
    harness.set_can_connect("uds-tokenizer", True)
    
    # Establish a tracing relation natively so `self.model.relations.get('tracing')` succeeds.
    harness.add_relation("tracing", "tempo")
    
    harness.update_config({"port": 8080, "hf-token": "MY_SECRET_TOKEN"})
    
    plan = harness.get_container_pebble_plan("llm-d-kv-cache")
    assert plan.services["llm-d-kv-cache"] is not None
    env = plan.services["llm-d-kv-cache"].environment
    assert env["HTTP_PORT"] == "8080"
    
    tk_plan = harness.get_container_pebble_plan("uds-tokenizer")
    tk_env = tk_plan.services["uds-tokenizer"].environment
    assert tk_env["HF_TOKEN"] == "MY_SECRET_TOKEN"
    assert tk_env["TOKENIZERS_DIR"] == "/tokenizers"
    
    assert isinstance(harness.model.unit.status, ops.ActiveStatus)
    
def test_relation_joined(harness):
    harness.set_can_connect("llm-d-kv-cache", True)
    rel_id = harness.add_relation("kv-cache-manager", "kv-manager")
    harness.add_relation_unit(rel_id, "kv-manager/0")
    
    # When binding, network details are attached. Let's add bind address mock
    harness.add_network("10.1.1.1", endpoint="kv-cache-manager")
    
    # Trigger relation changed/joined
    # By default ops testing auto-handles if we use add_relation properly.
    data = harness.get_relation_data(rel_id, harness.charm.unit)
    # The endpoint should be set
    assert "8000" in data["endpoint"]


def test_pebble_connection_failure(harness):
    harness.set_can_connect("llm-d-kv-cache", False)
    harness.update_config({})
    assert isinstance(harness.model.unit.status, ops.WaitingStatus)

@patch("ops.model.Container.push")
@patch("charm.TracingEndpointRequirer.is_ready")
@patch("charm.TracingEndpointRequirer.get_endpoint")
def test_pebble_error_writing_config(mock_get, mock_ready, mock_push, harness):
    mock_ready.return_value = False
    mock_get.return_value = ""
    harness.set_can_connect("llm-d-kv-cache", True)
    harness.set_can_connect("uds-tokenizer", True)
    
    mock_push.side_effect = ops.pebble.PathError("generic", "failed")
    
    with pytest.raises(ops.pebble.PathError):
        harness.update_config({"port": 8080})

@patch("ops.model.Container.add_layer")
def test_pebble_api_error(mock_add_layer, harness):
    harness.set_can_connect("llm-d-kv-cache", True)
    
    mock_add_layer.side_effect = ops.pebble.APIError(body={}, code=500, status="Error", message="api error")
    
    with pytest.raises(ops.pebble.APIError):
        harness.update_config({"port": 8080})
