"""Tests for OpcUaWaitForValue — mock backend."""
from __future__ import annotations

import time
from typing import Any, List

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.wait_for_value import OpcUaWaitForValue
from bteng_opcua.registry import NodeRegistry, reset_global_registry
from bteng_opcua.session import OpcUaSessionManager

SESSION = "test"

REGISTRY_DATA = {
    "nodes": [
        {"alias": "temp", "node_index": 2, "node_id": "s=Temperature", "data_type": "float"},
    ]
}


class MockBackend(OpcUaBackend):
    def __init__(self):
        self.connect_result = object()
        self.read_sequence: list[Any] = []   # if non-empty, pop left each call
        self.read_result = 99.5
        self.calls: list[str] = []

    async def connect(self, endpoint: str, **kwargs) -> Any:
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        pass

    async def read_node(self, client: Any, node_id: str) -> Any:
        self.calls.append(f"read:{node_id}")
        if self.read_sequence:
            return self.read_sequence.pop(0)
        return self.read_result

    async def write_node(self, client: Any, node_id: str, value: Any, variant_type: Any) -> None:
        pass

    async def call_method(self, client: Any, object_id: str, method_id: str, args: List[Any]) -> List[Any]:
        return []

    async def create_subscription(self, client: Any, node_id: str, on_change, period_ms: int = 500) -> Any:
        return object()

    async def delete_subscription(self, handle: Any) -> None:
        pass

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        self.calls.append(f"read_nodes:{len(node_ids)}")
        return [self.read_result] * len(node_ids)

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        return []


@pytest.fixture(autouse=True)
def isolated_manager(monkeypatch):
    backend = MockBackend()
    manager = OpcUaSessionManager(backend=backend)
    OpcUaSessionManager.set_instance(manager)
    yield manager, backend
    OpcUaSessionManager.reset_instance()


@pytest.fixture(autouse=True)
def clean_global_registry():
    reset_global_registry()
    yield
    reset_global_registry()


@pytest.fixture
def registry() -> NodeRegistry:
    reg = NodeRegistry()
    reg.load_dict(REGISTRY_DATA)
    return reg


@pytest.fixture
def bb() -> Blackboard:
    Blackboard.reset("wfv_bb")
    return Blackboard.create("wfv_bb")


def tick_until_done(node, max_ticks: int = 500) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)
    return NodeStatus.RUNNING


def inject_client(manager, backend):
    manager.register_client(SESSION, backend.connect_result)


# ---------------------------------------------------------------------------
# TestOpcUaWaitForValue
# ---------------------------------------------------------------------------

class TestOpcUaWaitForValue:

    def test_matching_value_returns_success_immediately(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 42.0

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Speed",
                "data_type": "float",
                "expected_value": "42.0",
                "output_key": "result_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("result_val")
        assert bb.get("result_val") == pytest.approx(42.0)

    def test_wrong_then_right_value_returns_success(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_sequence = [10.0, 42.0]

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Speed",
                "data_type": "float",
                "expected_value": "42.0",
                "output_key": "result_val",
                "poll_interval": "0.01",
            },
        ))
        status = tick_until_done(node, max_ticks=500)
        assert status == NodeStatus.SUCCESS
        assert bb.get("result_val") == pytest.approx(42.0)

    def test_timeout_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 0.0

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Speed",
                "data_type": "float",
                "expected_value": "99.0",
                "poll_interval": "0.01",
                "timeout": "0.05",
            },
        ))
        status = tick_until_done(node, max_ticks=500)
        assert status == NodeStatus.FAILURE
        assert "Timed out" in node.feedback_message

    def test_boolean_condition(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = True

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Flag",
                "data_type": "boolean",
                "expected_value": "true",
                "output_key": "flag_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("flag_val") is True

    def test_alias_resolves(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 23.0

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "temp",
                "registry": registry,
                "expected_value": "23.0",
                "output_key": "temp_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("temp_val") == pytest.approx(23.0)

    def test_output_key_stored(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 5.0

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Val",
                "data_type": "float",
                "expected_value": "5.0",
                "output_key": "my_key",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("my_key")
        assert bb.get("my_key") == pytest.approx(5.0)

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": "ghost",
                "node_id": "ns=2;s=X",
                "expected_value": "1",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_missing_node_id_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "",
                "node_id": "",
                "expected_value": "1",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_read_error_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        async def _failing_read(client, node_id):
            raise RuntimeError("io error")

        backend.read_node = _failing_read

        node = OpcUaWaitForValue("WFV", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Broken",
                "data_type": "float",
                "expected_value": "1.0",
                "poll_interval": "0.01",
            },
        ))
        status = tick_until_done(node, max_ticks=500)
        assert status == NodeStatus.FAILURE
        assert "io error" in node.feedback_message
