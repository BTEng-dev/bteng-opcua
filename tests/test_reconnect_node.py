"""Tests for the OpcUaReconnect BT node."""
from __future__ import annotations

import time
from typing import Any, List

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.reconnect import OpcUaReconnect
from bteng_opcua.registry import reset_global_registry
from bteng_opcua.session import OpcUaSessionManager

SESSION = "test_session"
ENDPOINT = "opc.tcp://mock:4840"


class MockBackend(OpcUaBackend):
    def __init__(self):
        self.connect_calls = 0
        self.connect_result = object()
        self.calls: list[str] = []

    async def connect(self, endpoint: str, **kwargs) -> Any:
        self.connect_calls += 1
        self.calls.append(f"connect:{endpoint}")
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        self.calls.append("disconnect")

    async def read_node(self, client, node_id): return None
    async def write_node(self, client, node_id, value, variant_type): pass
    async def call_method(self, client, object_id, method_id, args): return []
    async def create_subscription(self, client, node_id, on_change, period_ms=500): return object()
    async def delete_subscription(self, handle): pass
    async def read_nodes(self, client, node_ids): return [None] * len(node_ids)
    async def read_history(self, client, node_id, start_time, end_time, max_values=100): return []


@pytest.fixture(autouse=True)
def isolated_manager():
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
def bb() -> Blackboard:
    Blackboard.reset("test_bb")
    return Blackboard.create("test_bb")


# ── Helpers ───────────────────────────────────────────────────────────────────

def tick_until_done(node, max_ticks: int = 200) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)
    return NodeStatus.RUNNING


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOpcUaReconnectNode:
    def test_reconnect_restores_session(self, isolated_manager, bb):
        """Connect to save config, simulate a drop, reconnect → SUCCESS and 2 connect calls."""
        manager, backend = isolated_manager

        # Establish initial connection (saves config + registers client)
        manager.connect(SESSION, ENDPOINT)
        assert manager.is_connected(SESSION)
        assert backend.connect_calls == 1

        # Simulate connection drop: remove client without clearing config
        manager.unregister_client(SESSION)
        assert not manager.is_connected(SESSION)

        node = OpcUaReconnect("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = tick_until_done(node)

        assert status == NodeStatus.SUCCESS
        assert manager.is_connected(SESSION)
        assert backend.connect_calls == 2

    def test_reconnect_no_config_returns_failure(self, isolated_manager, bb):
        """No prior connect → immediate FAILURE with descriptive feedback."""
        node = OpcUaReconnect("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = node.execute_tick()

        assert status == NodeStatus.FAILURE
        assert "No saved config" in node.feedback_message

    def test_reconnect_replaces_stale_client(self, isolated_manager, bb):
        """Stale client in session is replaced by a fresh one after reconnect."""
        manager, backend = isolated_manager

        # Save config via connect
        manager.connect(SESSION, ENDPOINT)
        assert backend.connect_calls == 1

        # Inject a stale sentinel as the current client (simulates a broken connection)
        stale_client = object()
        manager.register_client(SESSION, stale_client)
        assert manager.is_connected(SESSION)

        node = OpcUaReconnect("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = tick_until_done(node)

        assert status == NodeStatus.SUCCESS
        assert manager.is_connected(SESSION)
        # The registered client must be the fresh one from backend, not the stale one
        actual_client = manager.get_client(SESSION)
        assert actual_client is not stale_client
        assert actual_client is backend.connect_result

    def test_reconnect_connect_error_returns_failure(self, isolated_manager, bb):
        """If the backend raises during reconnect, the node returns FAILURE."""
        manager, backend = isolated_manager

        # Save config
        manager.connect(SESSION, ENDPOINT)
        assert backend.connect_calls == 1

        # Simulate a dropped connection
        manager.unregister_client(SESSION)

        # Override connect to raise
        async def boom(endpoint, **kwargs):
            raise RuntimeError("timeout")

        backend.connect = boom

        node = OpcUaReconnect("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = tick_until_done(node)

        assert status == NodeStatus.FAILURE
        assert "timeout" in node.feedback_message

    def test_reconnect_preserves_session_config(self, isolated_manager, bb):
        """After a successful reconnect the session config is still available."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT)
        manager.unregister_client(SESSION)

        node = OpcUaReconnect("R", NodeConfig(
            blackboard=bb,
            params={"session_id": SESSION},
        ))
        status = tick_until_done(node)

        assert status == NodeStatus.SUCCESS
        config = manager.get_session_config(SESSION)
        assert config is not None
        assert config["endpoint"] == ENDPOINT
