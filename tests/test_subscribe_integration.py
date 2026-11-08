"""Integration tests for OpcUaSubscribe with a real in-process asyncua server.

Requires asyncua installed (not in the no-deps venv used for unit tests).
Run with: pytest tests/test_subscribe_integration.py -v
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest

from bteng import Blackboard, NodeConfig, NodeStatus
from bteng_opcua.nodes.subscribe import OpcUaSubscribe
from bteng_opcua.session import OpcUaSessionManager

ENDPOINT = "opc.tcp://127.0.0.1:48401/"
SESSION  = "integration"


# ── Server fixture ────────────────────────────────────────────────────────────

class _ServerContext:
    """asyncua server running in its own event loop in a background thread."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop = None
        self.server = None
        self.temp_var = None
        self.namespace_idx: int = 0
        self._ready = threading.Event()
        self._stop  = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(timeout=15), "asyncua server failed to start in 15s"

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def write_value(self, value: Any) -> None:
        """Write a new value to the temperature variable (thread-safe)."""
        async def _do():
            await self.temp_var.write_value(value)
        fut = asyncio.run_coroutine_threadsafe(_do(), self.loop)
        fut.result(timeout=5)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        from asyncua import Server, ua
        self.server = Server()
        await self.server.init()
        self.server.set_endpoint(ENDPOINT)
        idx = await self.server.register_namespace("http://bteng.test")
        self.namespace_idx = idx
        obj = await self.server.nodes.objects.add_object(idx, "TestDevice")
        # Explicit string NodeId so the node is reachable as ns=X;s=Temperature
        self.temp_var = await obj.add_variable(
            ua.NodeId("Temperature", idx), "Temperature", 20.0
        )
        await self.temp_var.set_writable()

        async with self.server:
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)


@pytest.fixture(scope="module")
def opcua_server():
    ctx = _ServerContext()
    ctx.start()
    yield ctx
    ctx.stop()


@pytest.fixture(scope="module")
def session(opcua_server):
    OpcUaSessionManager.reset_instance()
    manager = OpcUaSessionManager.get_instance()
    manager.connect(SESSION, ENDPOINT, timeout=10.0)
    yield SESSION
    manager.disconnect(SESSION)
    OpcUaSessionManager.reset_instance()


@pytest.fixture
def bb():
    Blackboard.reset("integ_bb")
    return Blackboard.create("integ_bb")


# ── Helpers ───────────────────────────────────────────────────────────────────

def tick_until_done(node, max_ticks: int = 500) -> NodeStatus:
    for _ in range(max_ticks):
        status = node.execute_tick()
        if status != NodeStatus.RUNNING:
            return status
        time.sleep(0.01)
    return NodeStatus.RUNNING


def make_sub_node(bb, session_id, node_id, **extra_params) -> OpcUaSubscribe:
    return OpcUaSubscribe("Sub", NodeConfig(
        blackboard=bb,
        params={"session_id": session_id, "node_id": node_id, **extra_params},
    ))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestOpcUaSubscribeIntegration:

    def _node_id(self, ctx: _ServerContext) -> str:
        return f"ns={ctx.namespace_idx};s=Temperature"

    def test_on_change_receives_value(self, opcua_server, session, bb):
        # asyncua fires an initial-value notification on subscription setup.
        # Write the target value first so the initial notification carries it.
        opcua_server.write_value(42.5)
        time.sleep(0.1)  # let server register the write

        node_id = self._node_id(opcua_server)
        node = make_sub_node(bb, session, node_id, output_key="temp", period_ms=100)
        status = tick_until_done(node)

        assert status == NodeStatus.SUCCESS
        assert bb.has("temp")
        assert abs(bb.get("temp") - 42.5) < 0.01

    def test_on_value_waits_for_match(self, opcua_server, session, bb):
        node_id = self._node_id(opcua_server)
        node = make_sub_node(
            bb, session, node_id,
            mode="on_value",
            expected_value=99.0,
            output_key="matched_temp",
            period_ms=100,
        )

        def _tick():
            tick_until_done(node)

        t = threading.Thread(target=_tick, daemon=True)
        t.start()

        time.sleep(0.5)
        # Write wrong value — node should keep RUNNING
        opcua_server.write_value(10.0)
        time.sleep(0.3)
        assert node.status == NodeStatus.RUNNING

        # Write target value
        opcua_server.write_value(99.0)
        t.join(timeout=10)
        assert node.status == NodeStatus.SUCCESS
        assert abs(bb.get("matched_temp") - 99.0) < 0.01

    def test_timeout_no_change(self, opcua_server, session, bb):
        node_id = self._node_id(opcua_server)
        # mode=on_value with an unreachable expected — will timeout
        node = make_sub_node(
            bb, session, node_id,
            mode="on_value",
            expected_value=-9999.0,
            timeout=0.5,
            period_ms=100,
        )
        status = tick_until_done(node)
        assert status == NodeStatus.FAILURE
        assert "timed out" in node.feedback_message
