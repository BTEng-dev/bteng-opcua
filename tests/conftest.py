"""Shared pytest fixtures for bteng_opcua integration tests.

The ``opcua_server`` fixture starts an in-process asyncua server in a
background thread and exposes metadata (URL, node IDs) to integration tests.

asyncua is an optional dependency — when it is absent the fixture is skipped
gracefully via ``pytest.importorskip``.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

import pytest

try:
    from asyncua import ua as _ua
except ImportError:
    _ua = None  # type: ignore[assignment]

# ── In-process asyncua server ─────────────────────────────────────────────────

ENDPOINT = "opc.tcp://127.0.0.1:48402/"
SESSION_ID = "integ_session"


class _ServerContext:
    """asyncua server running in its own event loop in a background thread.

    Exposes a ``write_value`` helper so tests can push values from the
    synchronous pytest thread without having to manage the event loop themselves.
    """

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: Any = None
        self.var_node: Any = None
        self.namespace_idx: int = 0
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="asyncua-test-server")

    # -- Lifecycle --

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(timeout=20), "asyncua test server failed to start within 20 s"

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    # -- Thread-safe helpers --

    def write_value(self, value: Any) -> None:
        """Write *value* to the test variable (thread-safe, blocks until done)."""
        async def _do() -> None:
            await self.var_node.write_value(_ua.Variant(value, _ua.VariantType.Int32))

        fut = asyncio.run_coroutine_threadsafe(_do(), self.loop)
        fut.result(timeout=5)

    # -- Background thread --

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        from asyncua import Server, ua  # noqa: PLC0415

        self.server = Server()
        await self.server.init()
        self.server.set_endpoint(ENDPOINT)

        idx = await self.server.register_namespace("http://bteng.integ.test")
        self.namespace_idx = idx

        obj = await self.server.nodes.objects.add_object(idx, "IntegTestDevice")

        # Use an integer NodeId so clients can address it as ns=<idx>;i=1000
        self.var_node = await obj.add_variable(
            ua.NodeId(1000, idx), "IntVar", ua.Variant(42, ua.VariantType.Int32)
        )
        await self.var_node.set_writable()

        async with self.server:
            self._ready.set()
            while not self._stop.is_set():
                await asyncio.sleep(0.05)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def opcua_server():
    """Session-scoped fixture: one in-process asyncua server for all tests.

    Skipped automatically when asyncua is not installed.
    """
    pytest.importorskip("asyncua")

    ctx = _ServerContext()
    ctx.start()
    yield ctx
    ctx.stop()


@pytest.fixture(scope="session")
def server_info(opcua_server: _ServerContext) -> Dict[str, Any]:
    """Return a dict with the server URL and variable node metadata."""
    idx = opcua_server.namespace_idx
    return {
        "url": ENDPOINT,
        "node_id": f"ns={idx};i=1000",
        "namespace": idx,
        "session_id": SESSION_ID,
    }
