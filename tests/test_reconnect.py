"""Tests for OpcUaSessionManager reconnect/resilience logic."""
from __future__ import annotations

from typing import Any, List

import pytest

from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.exceptions import ConnectionError as OpcUaConnectionError
from bteng_opcua.session import OpcUaSessionManager

SESSION = "test_session"
ENDPOINT = "opc.tcp://mock:4840"


class MockBackend(OpcUaBackend):
    """Minimal mock backend — only connect/disconnect needed for reconnect tests."""

    def __init__(self):
        self.connect_result = object()
        self.calls: list[str] = []

    async def connect(self, endpoint: str, **kwargs) -> Any:
        self.calls.append("connect")
        return self.connect_result

    async def disconnect(self, client: Any) -> None:
        self.calls.append("disconnect")

    async def read_node(self, client: Any, node_id: str) -> Any:
        return None

    async def write_node(self, client: Any, node_id: str, value: Any, variant_type: Any) -> None:
        pass

    async def call_method(self, client: Any, object_id: str, method_id: str, args: List[Any]) -> List[Any]:
        return []

    async def create_subscription(self, client: Any, node_id: str, on_change, period_ms: int = 500) -> Any:
        return object()

    async def delete_subscription(self, handle: Any) -> None:
        pass

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        return []

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        return []


@pytest.fixture(autouse=True)
def isolated_manager():
    """Each test gets a fresh session manager with the mock backend."""
    backend = MockBackend()
    manager = OpcUaSessionManager(backend=backend)
    OpcUaSessionManager.set_instance(manager)
    yield manager, backend
    OpcUaSessionManager.reset_instance()


class TestReconnect:
    def test_reconnect_after_disconnect(self, isolated_manager):
        """Connect, disconnect, then reconnect — session should be re-established."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT)
        assert manager.is_connected(SESSION)

        manager.disconnect(SESSION)
        assert not manager.is_connected(SESSION)

        # disconnect() removes the config, so we need to re-save it.
        # Per spec: reconnect reads saved config. After disconnect config is gone.
        # So re-connect first to restore config, then disconnect again, then reconnect
        # Actually: per spec disconnect() does pop _session_configs — let's re-connect
        # to save config, then test reconnect from a manually-cleared session state.
        #
        # The real test: connect saves config, we manually remove the session entry
        # (simulating a lost connection), then reconnect works.
        manager.connect(SESSION, ENDPOINT)
        assert manager.is_connected(SESSION)

        # Simulate connection drop: remove client from _sessions without going through disconnect()
        with manager._sessions_lock:
            manager._sessions.pop(SESSION, None)
        assert not manager.is_connected(SESSION)
        assert manager.get_session_config(SESSION) is not None

        manager.reconnect(SESSION)
        assert manager.is_connected(SESSION)
        assert backend.calls.count("connect") >= 2

    def test_reconnect_no_config_raises(self, isolated_manager):
        """Reconnecting an unknown session raises ConnectionError."""
        manager, backend = isolated_manager

        with pytest.raises(OpcUaConnectionError, match="No saved config"):
            manager.reconnect("unknown")

    def test_reconnect_stores_config_with_auth(self, isolated_manager):
        """connect() with username/password saves those credentials in the config."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT, username="u", password="p")

        config = manager.get_session_config(SESSION)
        assert config is not None
        assert config["endpoint"] == ENDPOINT
        assert config["username"] == "u"
        assert config["password"] == "p"

    def test_reconnect_after_unregister(self, isolated_manager):
        """unregister_client() removes the session but keeps the saved config."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT)
        assert manager.is_connected(SESSION)

        # unregister without disconnecting — config must be preserved
        client = manager.unregister_client(SESSION)
        assert client is not None
        assert not manager.is_connected(SESSION)

        # config is still there
        assert manager.get_session_config(SESSION) is not None

        # reconnect should succeed using the preserved config
        manager.reconnect(SESSION)
        assert manager.is_connected(SESSION)

    def test_get_session_config_returns_none_for_unknown(self, isolated_manager):
        """get_session_config() returns None when session has never been connected."""
        manager, backend = isolated_manager

        result = manager.get_session_config("never_connected")
        assert result is None

    def test_get_session_config_returns_copy(self, isolated_manager):
        """get_session_config() returns a copy — mutating it does not affect stored config."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT)
        config = manager.get_session_config(SESSION)
        config["endpoint"] = "mutated"

        # stored config must be unchanged
        fresh = manager.get_session_config(SESSION)
        assert fresh["endpoint"] == ENDPOINT

    def test_reconnect_clears_stale_session_entry(self, isolated_manager):
        """reconnect() removes any stale session entry before reconnecting."""
        manager, backend = isolated_manager

        manager.connect(SESSION, ENDPOINT)

        # Put a stale sentinel directly into _sessions
        stale_sentinel = object()
        with manager._sessions_lock:
            manager._sessions[SESSION] = stale_sentinel

        # reconnect should replace it with a fresh connection
        manager.reconnect(SESSION)

        assert manager.is_connected(SESSION)
        actual_client = manager.get_client(SESSION)
        assert actual_client is not stale_sentinel
