"""Tests for OpcUaBatchWrite — mock backend."""
from __future__ import annotations

import time
from typing import Any, List, Optional

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.batch_write import OpcUaBatchWrite
from bteng_opcua.session import OpcUaSessionManager

SESSION = "test"


class MockBackend(OpcUaBackend):
    def __init__(self):
        self.connect_result = object()
        self.written: list[tuple] = []  # list of (node_id, value)
        self.write_error: Optional[Exception] = None

    async def connect(self, endpoint: str, **kwargs) -> Any:
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        pass

    async def read_node(self, client: Any, node_id: str) -> Any:
        return None

    async def write_node(self, client: Any, node_id: str, value: Any, variant_type: Any) -> None:
        self.written.append((node_id, value))
        if self.write_error:
            raise self.write_error

    async def call_method(self, client: Any, object_id: str, method_id: str, args: List[Any]) -> List[Any]:
        return []

    async def create_subscription(self, client: Any, node_id: str, on_change, period_ms: int = 500) -> Any:
        return object()

    async def delete_subscription(self, handle: Any) -> None:
        pass

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        return [None] * len(node_ids)

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        return []


@pytest.fixture(autouse=True)
def isolated_manager(monkeypatch):
    backend = MockBackend()
    manager = OpcUaSessionManager(backend=backend)
    OpcUaSessionManager.set_instance(manager)
    yield manager, backend
    OpcUaSessionManager.reset_instance()


@pytest.fixture
def bb() -> Blackboard:
    Blackboard.reset("test_bw_bb")
    return Blackboard.create("test_bw_bb")


def tick_until_done(node, max_ticks: int = 200) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)
    return NodeStatus.RUNNING


def inject_client(manager, backend):
    manager.register_client(SESSION, backend.connect_result)


# ---------------------------------------------------------------------------
# OpcUaBatchWrite
# ---------------------------------------------------------------------------

class TestOpcUaBatchWrite:

    def test_writes_two_nodes(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("a", 1.0)
        bb.set("b", 2.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=A, ns=2;s=B",
                "input_keys": "a, b",
                "data_types": "float, float",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert ("ns=2;s=A", 1.0) in backend.written
        assert ("ns=2;s=B", 2.0) in backend.written

    def test_type_conversion_applied(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("flag", 1)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=Flag",
                "input_keys": "flag",
                "data_types": "boolean",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert len(backend.written) == 1
        assert backend.written[0][1] is True

    def test_missing_input_key_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=X",
                "input_keys": "missing_key",
                "data_types": "float",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE
        assert "missing_key" in node.feedback_message

    def test_length_mismatch_node_ids_input_keys_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("a", 1.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=A, ns=2;s=B",
                "input_keys": "a",
                "data_types": "float, float",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_missing_data_types_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("a", 1.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=A",
                "input_keys": "a",
                "data_types": "",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE
        assert "data_types" in node.feedback_message

    def test_data_types_length_mismatch_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("a", 1.0)
        bb.set("b", 2.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=A, ns=2;s=B",
                "input_keys": "a, b",
                "data_types": "float",  # only 1 type for 2 nodes
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_write_error_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.write_error = RuntimeError("rejected")

        bb.set("x", 42.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=X",
                "input_keys": "x",
                "data_types": "float",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "rejected" in node.feedback_message

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": "ghost",
                "node_ids":   "ns=2;s=A",
                "input_keys": "a",
                "data_types": "float",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_unknown_data_type_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)

        bb.set("a", 1.0)

        node = OpcUaBatchWrite("BW", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids":   "ns=2;s=A",
                "input_keys": "a",
                "data_types": "not_a_type",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE
