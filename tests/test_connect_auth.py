"""Tests for OpcUaConnect auth input ports — no real OPC UA server required."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, List

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.connect import OpcUaConnect
from bteng_opcua.registry import reset_global_registry
from bteng_opcua.session import OpcUaSessionManager

SESSION = "auth_test"


class AuthCapturingBackend(OpcUaBackend):
    """Backend that captures kwargs passed to connect() for assertion."""

    def __init__(self):
        self.captured_kwargs = {}
        self.connect_result = object()

    async def connect(self, endpoint: str, **kwargs) -> Any:
        self.captured_kwargs = kwargs
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        pass

    async def read_node(self, client: Any, node_id: str) -> Any:
        return None

    async def write_node(
        self,
        client: Any,
        node_id: str,
        value: Any,
        variant_type: Any,
    ) -> None:
        pass

    async def call_method(
        self,
        client: Any,
        object_id: str,
        method_id: str,
        args: List[Any],
    ) -> List[Any]:
        return []

    async def create_subscription(
        self,
        client: Any,
        node_id: str,
        on_change: Callable[[Any], None],
        period_ms: int = 500,
    ) -> Any:
        return (None, None)

    async def delete_subscription(self, handle: Any) -> None:
        pass

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        return []

    async def read_history(
        self,
        client: Any,
        node_id: str,
        start_time: datetime,
        end_time: datetime,
        max_values: int = 100,
    ) -> List[tuple]:
        return []


@pytest.fixture(autouse=True)
def isolated_manager(monkeypatch):
    """Each test gets a fresh session manager with the auth-capturing backend."""
    backend = AuthCapturingBackend()
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
    Blackboard.reset("auth_bb")
    return Blackboard.create("auth_bb")


def tick_until_done(node, max_ticks: int = 200) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)
    return NodeStatus.RUNNING


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOpcUaConnectAuth:
    def test_anonymous_connect_no_auth_kwargs(self, isolated_manager, bb):
        """No username/password params → username and password are None in kwargs."""
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={"endpoint": "opc.tcp://mock:4840", "session_id": SESSION},
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        # Empty-string defaults are converted to None
        assert backend.captured_kwargs.get("username") is None
        assert backend.captured_kwargs.get("password") is None

    def test_username_password_forwarded(self, isolated_manager, bb):
        """username and password params are forwarded as-is to backend.connect()."""
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={
                "endpoint":   "opc.tcp://mock:4840",
                "session_id": SESSION,
                "username":   "admin",
                "password":   "secret",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert backend.captured_kwargs["username"] == "admin"
        assert backend.captured_kwargs["password"] == "secret"

    def test_empty_string_becomes_none(self, isolated_manager, bb):
        """An explicit empty-string username is normalised to None."""
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={
                "endpoint":   "opc.tcp://mock:4840",
                "session_id": SESSION,
                "username":   "",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert backend.captured_kwargs.get("username") is None

    def test_certificate_path_forwarded(self, isolated_manager, bb):
        """certificate_path param is forwarded to backend.connect()."""
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={
                "endpoint":         "opc.tcp://mock:4840",
                "session_id":       SESSION,
                "certificate_path": "/tmp/cert.pem",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert backend.captured_kwargs["certificate_path"] == "/tmp/cert.pem"

    def test_connect_still_returns_success(self, isolated_manager, bb):
        """Providing auth params does not break the SUCCESS return path."""
        manager, backend = isolated_manager
        node = OpcUaConnect("C", NodeConfig(
            blackboard=bb,
            params={
                "endpoint":         "opc.tcp://mock:4840",
                "session_id":       SESSION,
                "username":         "admin",
                "password":         "s3cr3t",
                "certificate_path": "/tmp/cert.pem",
                "private_key_path": "/tmp/key.pem",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert manager.is_connected(SESSION)
