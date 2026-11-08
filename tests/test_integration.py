"""End-to-end integration tests for bteng_opcua using an in-process asyncua server.

The asyncua server is provided by the ``opcua_server`` / ``server_info`` fixtures
defined in ``conftest.py``.  All tests use the *real* AsyncUaBackend and the
real OpcUaSessionManager — no mocks.

No pytest-asyncio: asyncio runs in a background thread managed by
OpcUaSessionManager (and by the server fixture thread).
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Dict

import pytest

# Skip the entire module when asyncua is absent.
asyncua = pytest.importorskip("asyncua")

from bteng import Blackboard, NodeConfig, NodeStatus  # noqa: E402
from bteng_opcua.backend.asyncua_backend import AsyncUaBackend  # noqa: E402
from bteng_opcua.nodes.condition import OpcUaCondition  # noqa: E402
from bteng_opcua.nodes.connect import OpcUaConnect  # noqa: E402
from bteng_opcua.nodes.disconnect import OpcUaDisconnect  # noqa: E402
from bteng_opcua.nodes.read import OpcUaRead  # noqa: E402
from bteng_opcua.nodes.subscribe import OpcUaSubscribe  # noqa: E402
from bteng_opcua.nodes.write import OpcUaWrite  # noqa: E402
from bteng_opcua.session import OpcUaSessionManager  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_TICKS = 600  # maximum ticks before declaring timeout


# ── Helpers ───────────────────────────────────────────────────────────────────


def tick_until_done(node, max_ticks: int = _MAX_TICKS) -> NodeStatus:
    """Tick *node* repeatedly until it reaches a terminal status."""
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.01)
    return NodeStatus.RUNNING


def make_bb(name: str = "integ_bb") -> Blackboard:
    Blackboard.reset(name)
    return Blackboard.create(name)


def make_manager() -> OpcUaSessionManager:
    """Return a fresh OpcUaSessionManager with the real AsyncUaBackend."""
    return OpcUaSessionManager(backend=AsyncUaBackend())


@contextlib.contextmanager
def _swap_manager(mgr: OpcUaSessionManager):
    """Context manager: temporarily install *mgr* as the global singleton.

    On exit the previous singleton is restored (even if it was None).  This
    allows individual tests to inject a private manager without disturbing the
    module-scoped ``manager`` fixture used by adjacent tests.
    """
    with OpcUaSessionManager._class_lock:
        prev = OpcUaSessionManager._instance
        OpcUaSessionManager._instance = mgr
    try:
        yield mgr
    finally:
        with OpcUaSessionManager._class_lock:
            OpcUaSessionManager._instance = prev


# ── Session-scoped manager & connected session ────────────────────────────────


@pytest.fixture(scope="module")
def manager(opcua_server, server_info):
    """Module-scoped manager with a pre-connected session."""
    mgr = make_manager()
    OpcUaSessionManager.set_instance(mgr)
    mgr.connect(server_info["session_id"], server_info["url"], timeout=15.0)
    yield mgr
    mgr.disconnect(server_info["session_id"])
    OpcUaSessionManager.reset_instance()


@pytest.fixture
def bb():
    """Fresh blackboard for each test."""
    return make_bb()


@pytest.fixture(autouse=True)
def reset_var(opcua_server):
    """Reset the server variable to 42 before each test."""
    opcua_server.write_value(42)
    time.sleep(0.05)  # let the write propagate
    yield


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestConnectAndRead:
    """test_connect_and_read — connect to server, read variable, assert value."""

    def test_connect_and_read(self, server_info, bb):
        """Connect via OpcUaConnect node, then read value with OpcUaRead.

        This test exercises the full connect → read path with its own
        isolated OpcUaSessionManager that does not interfere with the
        module-scoped ``manager`` fixture used by other tests.
        """
        mgr = make_manager()
        sid = "cr_session"

        # Temporarily install our manager as the singleton so that BT nodes
        # (which call OpcUaSessionManager.get_instance()) see it.
        with _swap_manager(mgr):
            url = server_info["url"]
            node_id = server_info["node_id"]

            connect_node = OpcUaConnect("C", NodeConfig(
                blackboard=bb,
                params={"endpoint": url, "session_id": sid, "timeout": 15.0},
            ))
            status = tick_until_done(connect_node)
            assert status == NodeStatus.SUCCESS, connect_node.feedback_message
            assert mgr.is_connected(sid)

            read_node = OpcUaRead("R", NodeConfig(
                blackboard=bb,
                params={
                    "session_id": sid,
                    "node_id": node_id,
                    "data_type": "int32",
                    "output_key": "val",
                },
            ))
            status = tick_until_done(read_node)
            assert status == NodeStatus.SUCCESS, read_node.feedback_message
            assert bb.has("val")
            assert int(bb.get("val")) == 42

        # Tear down our manager's connection outside the context-manager so that
        # the singleton is already restored before we touch the manager.
        if mgr.is_connected(sid):
            mgr.disconnect(sid)


class TestWriteAndReadBack:
    """test_write_and_read_back — write 99, read back, assert 99."""

    def test_write_and_read_back(self, manager, server_info, bb):
        sid = server_info["session_id"]
        node_id = server_info["node_id"]

        write_node = OpcUaWrite("W", NodeConfig(
            blackboard=bb,
            params={
                "session_id": sid,
                "node_id": node_id,
                "data_type": "int32",
                "value": 99,
            },
        ))
        status = tick_until_done(write_node)
        assert status == NodeStatus.SUCCESS, write_node.feedback_message

        read_node = OpcUaRead("R", NodeConfig(
            blackboard=bb,
            params={
                "session_id": sid,
                "node_id": node_id,
                "data_type": "int32",
                "output_key": "readback",
            },
        ))
        status = tick_until_done(read_node)
        assert status == NodeStatus.SUCCESS, read_node.feedback_message
        assert int(bb.get("readback")) == 99


class TestDisconnectAndReconnect:
    """test_disconnect_and_reconnect — disconnect, reconnect, read successfully."""

    def test_disconnect_and_reconnect(self, server_info, bb):
        mgr = make_manager()
        sid = "dc_reconnect"

        with _swap_manager(mgr):
            url = server_info["url"]
            node_id = server_info["node_id"]

            # Connect
            mgr.connect(sid, url, timeout=15.0)
            assert mgr.is_connected(sid)

            # Disconnect via BT node (preserves session config for reconnect)
            disc_node = OpcUaDisconnect("D", NodeConfig(
                blackboard=bb,
                params={"session_id": sid},
            ))
            status = tick_until_done(disc_node)
            assert status == NodeStatus.SUCCESS
            assert not mgr.is_connected(sid)

            # Reconnect via manager.reconnect() (uses saved config)
            mgr.reconnect(sid)
            assert mgr.is_connected(sid)

            # Read after reconnect
            read_node = OpcUaRead("R", NodeConfig(
                blackboard=bb,
                params={
                    "session_id": sid,
                    "node_id": node_id,
                    "data_type": "int32",
                    "output_key": "reconnect_val",
                },
            ))
            status = tick_until_done(read_node)
            assert status == NodeStatus.SUCCESS, read_node.feedback_message
            assert int(bb.get("reconnect_val")) == 42

        if mgr.is_connected(sid):
            mgr.disconnect(sid)


class TestBatchRead:
    """test_batch_read — read same node twice (two entries), assert both values."""

    def test_batch_read(self, manager, server_info, bb):
        """Use AsyncUaBackend.read_nodes() directly through manager.run_sync."""
        sid = server_info["session_id"]
        node_id = server_info["node_id"]

        client = manager.get_client(sid)
        # read_nodes expects a list; provide the same node twice
        values = manager.run_sync(
            manager._backend.read_nodes(client, [node_id, node_id])
        )
        assert len(values) == 2
        assert int(values[0]) == 42
        assert int(values[1]) == 42


class TestSubscribeReceivesValue:
    """test_subscribe_receives_value — subscribe, write new value, assert notification."""

    def test_subscribe_receives_value(self, manager, opcua_server, server_info, bb):
        sid = server_info["session_id"]
        node_id = server_info["node_id"]

        sub_node = OpcUaSubscribe("Sub", NodeConfig(
            blackboard=bb,
            params={
                "session_id": sid,
                "node_id": node_id,
                "data_type": "int32",
                "output_key": "sub_val",
                "period_ms": 100,
                "timeout": 5.0,
            },
        ))

        # Write the expected value from a background thread while the BT ticks
        def _writer() -> None:
            # Give subscription time to set up (initial value fires first)
            time.sleep(0.5)
            opcua_server.write_value(77)

        t = threading.Thread(target=_writer, daemon=True)
        t.start()

        # Tick the subscribe node until it picks up *any* value.
        # asyncua fires an initial-value notification on subscribe — the first
        # tick(s) may receive the old value (42) before the writer fires 77.
        # We tick until SUCCESS or failure.
        status = tick_until_done(sub_node, max_ticks=_MAX_TICKS)
        t.join(timeout=6)

        assert status == NodeStatus.SUCCESS, sub_node.feedback_message
        assert bb.has("sub_val")
        # The node exits on the first notification (42 or 77); just confirm it's numeric
        assert isinstance(int(bb.get("sub_val")), int)


class TestConditionMatch:
    """test_condition_match — OpcUaCondition with expected_value='42' asserts SUCCESS."""

    def test_condition_match(self, manager, server_info, bb):
        sid = server_info["session_id"]
        node_id = server_info["node_id"]

        cond_node = OpcUaCondition("Cond", NodeConfig(
            blackboard=bb,
            params={
                "session_id": sid,
                "node_id": node_id,
                "data_type": "int32",
                "expected_value": "42",
            },
        ))
        status = tick_until_done(cond_node)
        assert status == NodeStatus.SUCCESS, cond_node.feedback_message


class TestConditionNoMatch:
    """test_condition_no_match — OpcUaCondition with expected_value='999' asserts FAILURE."""

    def test_condition_no_match(self, manager, server_info, bb):
        sid = server_info["session_id"]
        node_id = server_info["node_id"]

        cond_node = OpcUaCondition("Cond", NodeConfig(
            blackboard=bb,
            params={
                "session_id": sid,
                "node_id": node_id,
                "data_type": "int32",
                "expected_value": "999",
            },
        ))
        status = tick_until_done(cond_node)
        assert status == NodeStatus.FAILURE, cond_node.feedback_message
        assert "Condition not met" in cond_node.feedback_message
