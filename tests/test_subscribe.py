"""Tests for OpcUaSubscribe BT node — uses a mock backend, no real OPC UA server required."""
from __future__ import annotations

import asyncio
import concurrent.futures
import time
from typing import Any, List, Optional

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.nodes.subscribe import OpcUaSubscribe
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
    ]
}

# ---------------------------------------------------------------------------
# Sentinel handle returned by create_subscription
# ---------------------------------------------------------------------------

class _SubscriptionHandle:
    """Opaque sentinel returned by MockBackend.create_subscription."""


# ---------------------------------------------------------------------------
# Extended MockBackend
# ---------------------------------------------------------------------------

class MockBackend(OpcUaBackend):
    """In-memory mock that records calls and returns configurable responses."""

    def __init__(self):
        self.connect_result = object()
        self.read_result = 99.5
        self.write_error = None
        self.method_result = [42, True]
        self.condition_read = True
        self.calls: list[str] = []

        # subscription controls
        self.subscription_value = None   # value to fire on subscription
        self.subscription_delay = 0.0    # seconds before firing (0 = immediate on setup)
        self.subscription_error = None   # if set, raise in create_subscription

        # tracking
        self.subscribed_callbacks: list[tuple] = []   # list of (node_id, cb) tuples
        self.deleted_handles: list = []

    # -- Standard OpcUaBackend methods --

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

    # -- New subscription methods --

    async def create_subscription(self, client: Any, node_id: str, on_change_cb, period_ms: int = 500) -> Any:
        """Create a subscription and optionally fire the callback immediately."""
        if self.subscription_error is not None:
            raise self.subscription_error

        self.subscribed_callbacks.append((node_id, on_change_cb))

        if self.subscription_delay == 0.0 and self.subscription_value is not None:
            # Fire the callback immediately before returning
            on_change_cb(self.subscription_value)
        elif self.subscription_delay > 0.0:
            # Schedule firing after a delay
            delay = self.subscription_delay
            value = self.subscription_value
            if value is not None:
                loop = asyncio.get_event_loop()
                async def _delayed_fire():
                    await asyncio.sleep(delay)
                    on_change_cb(value)
                loop.create_task(_delayed_fire())

        return _SubscriptionHandle()

    async def delete_subscription(self, handle: Any) -> None:
        self.calls.append("delete_subscription")
        self.deleted_handles.append(handle)

    async def read_nodes(self, client: Any, node_ids: List[str]) -> List[Any]:
        return [self.read_result for _ in node_ids]

    async def read_history(self, client: Any, node_id: str, start_time, end_time, max_values: int = 100) -> List[tuple]:
        return []


# ---------------------------------------------------------------------------
# Helper: fire subscription callbacks from a test
# ---------------------------------------------------------------------------

def fire_subscription(backend: MockBackend, value: Any) -> None:
    """Simulate a server-sent notification by invoking all registered callbacks."""
    for _, cb in backend.subscribed_callbacks:
        cb(value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tick_until_done(node, max_ticks: int = 200) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.005)  # yield to the event loop thread between ticks
    return NodeStatus.RUNNING


def inject_client(manager: OpcUaSessionManager, backend: MockBackend) -> None:
    manager.register_client(SESSION, backend.connect_result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOpcUaSubscribe:

    def test_on_change_returns_success_after_notification(self, isolated_manager, bb):
        """mode=on_change: fires callback once → SUCCESS, value stored in blackboard."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = 42.0

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temperature",
                "data_type": "float",
                "mode": "on_change",
                "output_key": "temp_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("temp_val")
        assert bb.get("temp_val") == pytest.approx(42.0)

    def test_on_change_value_stored_with_output_key(self, isolated_manager, bb):
        """output_key port → value stored under that blackboard key."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = 7

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Counter",
                "mode": "on_change",
                "output_key": "my_custom_key",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.has("my_custom_key")
        assert bb.get("my_custom_key") == 7

    def test_on_value_waits_until_match(self, isolated_manager, bb):
        """mode=on_value: wrong value → still RUNNING; correct value → SUCCESS."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        # Do not fire on setup; we'll fire manually
        backend.subscription_value = None

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Flag",
                "mode": "on_value",
                "expected_value": "ready",
                "output_key": "flag_val",
            },
        ))

        # First tick → on_start → RUNNING
        status = node.execute_tick()
        assert status == NodeStatus.RUNNING

        # Wait for setup to complete (subscription registered)
        deadline = time.time() + 2.0
        while len(backend.subscribed_callbacks) == 0 and time.time() < deadline:
            time.sleep(0.005)
        assert len(backend.subscribed_callbacks) > 0, "Subscription never registered"

        # Fire wrong value → should stay RUNNING
        fire_subscription(backend, "not_ready")
        time.sleep(0.02)
        status = node.execute_tick()
        assert status == NodeStatus.RUNNING

        # Fire correct value → should become SUCCESS
        fire_subscription(backend, "ready")
        time.sleep(0.02)
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS

    def test_on_value_correct_first_time(self, isolated_manager, bb):
        """mode=on_value: matching value fired immediately → SUCCESS."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = "done"

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Status",
                "mode": "on_value",
                "expected_value": "done",
                "output_key": "status_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("status_val") == "done"

    def test_timeout_returns_failure_when_no_notification(self, isolated_manager, bb):
        """timeout > 0, no callback fired → FAILURE after deadline."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        # No value will be fired
        backend.subscription_value = None

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Slow",
                "mode": "on_change",
                "timeout": 0.05,   # 50 ms
                "output_key": "slow_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_timeout_returns_failure_during_setup(self, isolated_manager, bb):
        """create_subscription hangs → timeout fires → FAILURE."""
        manager, backend = isolated_manager
        inject_client(manager, backend)

        # Replace create_subscription with a version that hangs forever
        async def hanging_create(client, node_id, on_change_cb, period_ms=500):
            await asyncio.sleep(9999)
            return _SubscriptionHandle()

        backend.create_subscription = hanging_create

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Hanging",
                "mode": "on_change",
                "timeout": 0.05,   # 50 ms
                "output_key": "hanging_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_session_not_found_returns_failure(self, isolated_manager, bb):
        """Session not registered → on_start returns FAILURE immediately."""
        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": "nonexistent_session",
                "node_id": "ns=2;s=Temperature",
                "mode": "on_change",
                "output_key": "temp_val",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_missing_node_id_returns_failure(self, isolated_manager, bb):
        """No alias and no node_id → FAILURE."""
        manager, backend = isolated_manager
        inject_client(manager, backend)

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "",
                "node_id": "",
                "mode": "on_change",
                "output_key": "some_val",
            },
        ))
        status = node.execute_tick()
        assert status == NodeStatus.FAILURE

    def test_halted_calls_delete_subscription(self, isolated_manager, bb):
        """Start node, tick once (RUNNING), halt → delete_subscription called."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        # Subscription fires immediately so that we can get it into on_running phase
        # But we want to halt while it's still RUNNING — so don't fire immediately.
        backend.subscription_value = None

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temperature",
                "mode": "on_change",
                "output_key": "temp_val",
            },
        ))

        # Tick once — should be RUNNING
        status = node.execute_tick()
        assert status == NodeStatus.RUNNING

        # Wait a bit for subscription setup to proceed, then halt
        time.sleep(0.05)
        node.halt()

        # Give time for delete_subscription to be called
        deadline = time.time() + 2.0
        while "delete_subscription" not in backend.calls and time.time() < deadline:
            time.sleep(0.01)

        assert "delete_subscription" in backend.calls

    def test_subscription_error_returns_failure(self, isolated_manager, bb):
        """create_subscription raises RuntimeError → FAILURE."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_error = RuntimeError("subscription refused")

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temperature",
                "mode": "on_change",
                "output_key": "temp_val",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE

    def test_node_alias_resolves_and_converts_type(self, isolated_manager, bb, registry):
        """Alias 'temp' (float) — raw int 23 from callback → stored as 23.0."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = 23   # raw int from callback

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_alias": "temp",
                "registry": registry,
                "mode": "on_change",
                "output_key": "temp_converted",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        result = bb.get("temp_converted")
        assert isinstance(result, float)
        assert result == pytest.approx(23.0)

    def test_persistent_reuses_subscription(self, isolated_manager, bb):
        """persistent=true: second activation reuses the existing subscription handle."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = None  # fire manually

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Counter",
                "mode": "on_change",
                "output_key": "counter",
                "persistent": "true",
            },
        ))

        # First activation
        status = node.execute_tick()
        assert status == NodeStatus.RUNNING
        # Wait for subscription to be registered
        deadline = time.time() + 2.0
        while len(backend.subscribed_callbacks) == 0 and time.time() < deadline:
            time.sleep(0.005)
        assert len(backend.subscribed_callbacks) > 0

        fire_subscription(backend, 1)
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("counter") == 1

        # Second activation — should NOT create a new subscription
        subscription_count_before = len(backend.subscribed_callbacks)
        # Kick off second activation: on_start resets queue and returns RUNNING
        status = node.execute_tick()
        assert status == NodeStatus.RUNNING
        # Now fire after the fresh queue is in place
        fire_subscription(backend, 2)
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        assert bb.get("counter") == 2
        # Still same number of registered callbacks (no new subscription created)
        assert len(backend.subscribed_callbacks) == subscription_count_before

    def test_persistent_false_deletes_subscription_on_success(self, isolated_manager, bb):
        """persistent=false (default): subscription deleted after first SUCCESS."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = 42.0

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temp",
                "mode": "on_change",
                "output_key": "t",
                "persistent": "false",
            },
        ))
        status = tick_until_done(node)
        assert status == NodeStatus.SUCCESS
        # delete_subscription is submitted asynchronously — wait briefly
        deadline = time.time() + 2.0
        while "delete_subscription" not in backend.calls and time.time() < deadline:
            time.sleep(0.01)
        assert "delete_subscription" in backend.calls

    def test_persistent_halted_cleans_up(self, isolated_manager, bb):
        """persistent=true: halt() still deletes the subscription."""
        manager, backend = isolated_manager
        inject_client(manager, backend)
        backend.subscription_value = None

        node = OpcUaSubscribe("S", NodeConfig(
            blackboard=bb,
            params={
                "session_id": SESSION,
                "node_id": "ns=2;s=Temp",
                "mode": "on_change",
                "output_key": "t",
                "persistent": "true",
            },
        ))
        node.execute_tick()
        time.sleep(0.05)
        node.halt()

        deadline = time.time() + 2.0
        while "delete_subscription" not in backend.calls and time.time() < deadline:
            time.sleep(0.01)
        assert "delete_subscription" in backend.calls
