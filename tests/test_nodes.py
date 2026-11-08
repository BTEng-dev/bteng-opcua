"""Tests for BT nodes — uses a mock backend, no real OPC UA server required."""
from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.exceptions import ConditionNotSatisfiedError, MethodCallError
from bteng_opcua.nodes.call_method import OpcUaCallMethod
from bteng_opcua.nodes.connect import OpcUaConnect
from bteng_opcua.nodes.disconnect import OpcUaDisconnect
from bteng_opcua.nodes.read import OpcUaRead
from bteng_opcua.nodes.write import OpcUaWrite
from bteng_opcua.registry import NodeRegistry, reset_global_registry
from bteng_opcua.session import OpcUaSessionManager

SESSION = "test"

REGISTRY_DATA = {
    "nodes": [
        {
            "alias": "temp",
            "node_index": 2,
            "node_id": "s=Temperature",
            "data_type": "float",
        },
        {
            "alias": "move",
            "node_index": 2,
            "node_id": "",
            "data_type": "method",
            "object_id": "ns=2;i=100",
            "method_id": "ns=2;i=101",
            "input_args": ["x:double", "y:double"],
            "output_args": ["code:int32", "ok:boolean"],
        },
        {
            "alias": "move_conditional",
            "node_index": 2,
            "node_id": "",
            "data_type": "method",
            "object_id": "ns=2;i=200",
            "method_id": "ns=2;i=201",
            "condition_node_id": "ns=2;i=50",
            "condition_value": True,
            "input_args": [],
            "output_args": [],
        },
    ]
}


class MockBackend(OpcUaBackend):
    """In-memory mock that records calls and returns configurable responses."""

    def __init__(self):
        self.connect_result = object()
        self.read_result    = 99.5
        self.write_error    = None
        self.method_result  = [42, True]
        self.condition_read = True
        self.calls: list[str] = []

    async def connect(self, endpoint: str, **kwargs) -> Any:
        self.calls.append("connect")
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        self.calls.append("disconnect")

    async def read_node(self, client: Any, node_id: str) -> Any:
        self.calls.append(f"read:{node_id}")
        return self.condition_read if "50" in node_id else self.read_result

    async def write_node(self, client: Any, node_id: str, value: Any, variant_type: Any) -> None:
        self.calls.append(f"write:{node_id}={value}")
        if self.write_error:
            raise self.write_error

    async def call_method(self, client: Any, object_id: str, method_id: str, args: List[Any]) -> List[Any]:
        self.calls.append(f"call:{method_id}")
        return self.method_result

    async def create_subscription(self, client: Any, node_id: str, on_change, period_ms: int = 500) -> Any:
        return object()

    async def delete_subscription(self, handle: Any) -> None:
        pass

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        return [self.read_result for _ in node_ids]

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        return []


@pytest.fixture(autouse=True)
def isolated_manager(monkeypatch):
    """Each test gets a fresh session manager with the mock backend."""
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
    Blackboard.reset("test_bb")
    return Blackboard.create("test_bb")


# ── Helpers ───────────────────────────────────────────────────────────────────

def tick_until_done(node, max_ticks: int = 200) -> NodeStatus:
    import time
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)  # yield to the event loop thread between ticks
    return NodeStatus.RUNNING


def inject_client(manager: OpcUaSessionManager, backend: MockBackend) -> None:
    manager.register_client(SESSION, backend.connect_result)


# ── OpcUaConnect ──────────────────────────────────────────────────────────────

class TestOpcUaConnect:
    def test_connects_and_returns_success(self, isolated_manager, bb):
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={"endpoint": "opc.tcp://mock:4840", "session_id": SESSION},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert manager.is_connected(SESSION)
        assert "connect" in backend.calls

    def test_already_connected_returns_success_immediately(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={"endpoint": "opc.tcp://mock:4840", "session_id": SESSION},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.SUCCESS
        assert "connect" not in backend.calls

    def test_missing_endpoint_returns_failure(self, isolated_manager, bb):
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_connect_error_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager

        async def boom(endpoint, **kw):
            raise RuntimeError("timeout")

        backend.connect = boom
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={"endpoint": "opc.tcp://mock:4840", "session_id": SESSION},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "timeout" in node.feedback_message


# ── OpcUaDisconnect ───────────────────────────────────────────────────────────

class TestOpcUaDisconnect:
    def test_disconnects_registered_session(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaDisconnect("D", NodeConfig(
            blackboard=bb, params={"session_id": SESSION}
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert not manager.is_connected(SESSION)
        assert "disconnect" in backend.calls

    def test_not_connected_returns_success(self, isolated_manager, bb):
        node = OpcUaDisconnect("D", NodeConfig(
            blackboard=bb, params={"session_id": SESSION}
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS


# ── OpcUaRead ─────────────────────────────────────────────────────────────────

class TestOpcUaRead:
    def test_reads_value_to_blackboard(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 23.5
        node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "temp", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("temp") == pytest.approx(23.5)

    def test_reads_with_output_key(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "temp",
                "output_key": "sensor_temp",
                "registry":   registry,
            },
        ))
        tick_until_done(node)
        assert bb.has("sensor_temp")

    def test_session_not_found_returns_failure(self, isolated_manager, bb, registry):
        node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={"session_id": "missing", "node_alias": "temp", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_raw_node_id_without_alias(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id":    "s=Temperature",
                "node_index": 2,
                "data_type":  "float",
                "output_key": "raw_temp",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("raw_temp")

    def test_method_alias_in_read_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "move", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "method" in node.feedback_message.lower()


# ── OpcUaWrite ────────────────────────────────────────────────────────────────

class TestOpcUaWrite:
    def test_writes_value(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaWrite("W", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "temp", "value": 99.9, "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert any("write" in c for c in backend.calls)

    def test_write_error_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.write_error = RuntimeError("write rejected")
        node = OpcUaWrite("W", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "temp", "value": 1.0, "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_missing_value_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaWrite("W", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "temp", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_method_alias_in_write_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaWrite("W", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_alias": "move", "value": 1.0, "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "method" in node.feedback_message.lower()


# ── OpcUaCallMethod ───────────────────────────────────────────────────────────

class TestOpcUaCallMethod:
    def test_calls_method_and_stores_outputs(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.method_result = [7, True]
        bb.set("x", 1.0)
        bb.set("y", 2.0)
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "method_alias": "move", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("code") == 7
        assert bb.get("ok") is True
        assert "call:ns=2;i=101" in backend.calls

    def test_output_prefix_applied(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.method_result = [0, False]
        bb.set("x", 0.0)
        bb.set("y", 0.0)
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={
                "session_id":    SESSION,
                "method_alias":  "move",
                "output_prefix": "move_",
                "registry":      registry,
            },
        ))
        tick_until_done(node)
        assert bb.has("move_code")
        assert bb.has("move_ok")

    def test_missing_alias_returns_failure(self, isolated_manager, bb):
        inject_client(isolated_manager[0], isolated_manager[1])
        node = OpcUaCallMethod("M", NodeConfig(blackboard=bb, params={"session_id": SESSION}))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_data_node_alias_returns_failure(self, isolated_manager, bb, registry):
        inject_client(isolated_manager[0], isolated_manager[1])
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "method_alias": "temp", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "data node" in node.feedback_message

    def test_condition_satisfied(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.condition_read = True
        backend.method_result  = []
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "method_alias": "move_conditional", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_condition_not_satisfied_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.condition_read = False  # condition node value != True
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "method_alias": "move_conditional", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "Condition" in node.feedback_message

    def test_method_call_error_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        bb.set("x", 1.0)
        bb.set("y", 1.0)

        async def boom(client, obj, method, args):
            raise MethodCallError("server busy")

        backend.call_method = boom
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "method_alias": "move", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "server busy" in node.feedback_message

    def test_session_not_found_returns_failure(self, isolated_manager, bb, registry):
        node = OpcUaCallMethod("M", NodeConfig(
            blackboard=bb,
            params={"session_id": "gone", "method_alias": "move", "registry": registry},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
