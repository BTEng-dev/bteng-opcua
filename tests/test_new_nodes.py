"""Tests for OpcUaBatchRead, OpcUaReadHistory, OpcUaCondition — mock backend."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, List

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.batch_read import OpcUaBatchRead
from bteng_opcua.nodes.condition import OpcUaCondition
from bteng_opcua.nodes.read_history import OpcUaReadHistory
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
            "input_args": [],
            "output_args": [],
        },
    ]
}


class MockBackend(OpcUaBackend):
    def __init__(self):
        self.connect_result = object()
        self.read_result = 99.5
        self.batch_results: list[Any] = []
        self.history_results: list[tuple] = []
        self.calls: list[str] = []

    async def connect(self, endpoint: str, **kwargs) -> Any:
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        pass

    async def read_node(self, client: Any, node_id: str) -> Any:
        self.calls.append(f"read:{node_id}")
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
        return list(self.batch_results) if self.batch_results else [self.read_result] * len(node_ids)

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        self.calls.append(f"read_history:{node_id}")
        return list(self.history_results)


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
    Blackboard.reset("test_bb")
    return Blackboard.create("test_bb")


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
# OpcUaBatchRead
# ---------------------------------------------------------------------------

class TestOpcUaBatchRead:

    def test_reads_two_nodes_to_blackboard(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.batch_results = [10.0, 20.0]

        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids": "ns=2;s=Temp, ns=2;s=Pressure",
                "output_keys": "temp, pressure",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("temp") == pytest.approx(10.0)
        assert bb.get("pressure") == pytest.approx(20.0)
        assert "read_nodes:2" in backend.calls

    def test_type_conversion_applied(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.batch_results = [7, 1]

        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids": "ns=2;s=A, ns=2;s=B",
                "output_keys": "a_val, b_val",
                "data_types": "float, boolean",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert isinstance(bb.get("a_val"), float)
        assert bb.get("b_val") is True

    def test_missing_node_ids_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_ids": "", "output_keys": "k"},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_length_mismatch_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids": "ns=2;s=A, ns=2;s=B",
                "output_keys": "only_one",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": "ghost",
                "node_ids": "ns=2;s=A",
                "output_keys": "a",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_unknown_data_type_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids": "ns=2;s=A",
                "output_keys": "a",
                "data_types": "not_a_type",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_data_types_length_mismatch_returns_failure(self, isolated_manager, bb):
        """data_types list shorter than node_ids → FAILURE."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_ids": "ns=2;s=A, ns=2;s=B",
                "output_keys": "a, b",
                "data_types": "float",  # only 1 type for 2 nodes
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_alias_resolves_node_ids_and_types(self, isolated_manager, bb, registry):
        """node_aliases resolves node_ids and data_types from registry."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.batch_results = [23]  # raw int → should be converted to float via alias type

        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_aliases": "temp",
                "registry": registry,
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        result = bb.get("temp")   # output_key defaults to alias name
        assert isinstance(result, float)
        assert result == pytest.approx(23.0)

    def test_alias_uses_explicit_output_keys(self, isolated_manager, bb, registry):
        """node_aliases with explicit output_keys stores under those keys."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.batch_results = [5.0]

        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_aliases": "temp",
                "output_keys": "temperature_reading",
                "registry": registry,
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("temperature_reading")

    def test_method_alias_in_batch_read_returns_failure(self, isolated_manager, bb, registry):
        """Method alias in node_aliases → FAILURE."""
        manager, backend = isolated_manager
        inject_client(manager, backend)

        node = OpcUaBatchRead("BR", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_aliases": "move",
                "registry": registry,
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE
        assert "method" in node.feedback_message.lower()


# ---------------------------------------------------------------------------
# OpcUaReadHistory
# ---------------------------------------------------------------------------

class TestOpcUaReadHistory:

    def test_stores_history_list_on_blackboard(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        backend.history_results = [(ts, 42.0), (ts, 43.0)]

        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temperature",
                "output_key": "temp_history",
                "duration_seconds": 300,
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        result = bb.get("temp_history")
        assert len(result) == 2
        assert result[0][1] == pytest.approx(42.0)
        assert "read_history:ns=2;s=Temperature" in backend.calls

    def test_empty_history_returns_success(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.history_results = []

        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temperature",
                "output_key": "hist",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("hist") == []

    def test_missing_node_id_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_id": ""},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={"session_id": "ghost", "node_id": "ns=2;s=Temp"},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_invalid_end_time_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temp",
                "end_time": "not-a-date",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_default_output_key_is_history(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.history_results = []

        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_id": "ns=2;s=Temp"},
        ))
        tick_until_done(node)
        assert bb.has("history")

    def test_z_suffix_end_time_accepted(self, isolated_manager, bb):
        """ISO 8601 'Z' suffix in end_time should be accepted on Python 3.10+."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.history_results = []

        node = OpcUaReadHistory("H", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temp",
                "output_key": "hist",
                "end_time": "2024-01-01T12:00:00Z",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS


# ---------------------------------------------------------------------------
# OpcUaCondition
# ---------------------------------------------------------------------------

class TestOpcUaCondition:

    def test_matching_value_returns_success(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 99.5

        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Speed",
                "data_type": "float",
                "expected_value": "99.5",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_non_matching_value_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 50.0

        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Speed",
                "data_type": "float",
                "expected_value": "99.5",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "99.5" in node.feedback_message

    def test_boolean_condition_true(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = True

        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Flag",
                "data_type": "boolean",
                "expected_value": "true",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_integer_condition(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 7

        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Count",
                "data_type": "int32",
                "expected_value": "7",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_alias_resolves(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.read_result = 23.0

        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "temp",
                "registry": registry,
                "expected_value": "23.0",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_missing_node_id_returns_failure(self, isolated_manager, bb):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION, "node_id": "", "expected_value": "1"},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={"session_id": "ghost", "node_id": "ns=2;s=X", "expected_value": "1"},
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_method_alias_in_condition_returns_failure(self, isolated_manager, bb, registry):
        manager, backend = isolated_manager
        inject_client(manager, backend)
        node = OpcUaCondition("C", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "move",
                "registry": registry,
                "expected_value": "1",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "method" in node.feedback_message.lower()
