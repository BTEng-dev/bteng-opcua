"""OPC UA session manager — background asyncio loop + multi-session lifecycle."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from typing import TYPE_CHECKING, Any

from bteng_opcua.exceptions import ConnectionError, SessionNotFoundError

if TYPE_CHECKING:
    from bteng_opcua.backend.base import OpcUaBackend


class OpcUaSessionManager:
    """Manages named OPC UA client sessions via a background asyncio event loop.

    A single background thread runs an asyncio event loop for all I/O.
    BT nodes submit coroutines from the BT (synchronous) thread and either
    block (run_sync) or poll (submit + Future.done()) for completion.
    """

    _instance: OpcUaSessionManager | None = None
    _class_lock = threading.Lock()

    def __init__(self, backend: OpcUaBackend | None = None) -> None:
        self._sessions: dict[str, Any] = {}
        self._sessions_lock = threading.Lock()
        self._session_configs: dict[str, dict] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="bteng-opcua-loop",
        )
        self._thread.start()
        self._backend: OpcUaBackend = backend or self._default_backend()

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> OpcUaSessionManager:
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Stop the background loop and clear the singleton (useful in tests)."""
        with cls._class_lock:
            if cls._instance is not None:
                cls._instance._loop.call_soon_threadsafe(cls._instance._loop.stop)
                cls._instance._session_configs.clear()
                cls._instance = None

    @classmethod
    def set_instance(cls, manager: OpcUaSessionManager) -> None:
        """Inject a custom manager (e.g. with a mock backend) in tests."""
        with cls._class_lock:
            cls._instance = manager

    # ── Async bridge ──────────────────────────────────────────────────────────

    def submit(self, coro) -> concurrent.futures.Future:
        """Schedule a coroutine on the background loop; return a Future immediately."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run_sync(self, coro) -> Any:
        """Schedule a coroutine and block until it completes or raises."""
        return self.submit(coro).result()

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def connect(self, session_id: str, endpoint: str, **kwargs) -> None:
        """Connect to an OPC UA endpoint and register the session.

        No-op if the session is already connected.
        Raises ConnectionError on failure.
        """
        with self._sessions_lock:
            if session_id in self._sessions:
                return
        try:
            client = self.run_sync(self._backend.connect(endpoint, **kwargs))
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect session '{session_id}' to {endpoint!r}: {exc}"
            ) from exc
        with self._sessions_lock:
            self._sessions[session_id] = client
            self._session_configs[session_id] = {"endpoint": endpoint, **kwargs}

    def disconnect(self, session_id: str) -> None:
        """Disconnect and remove a session. Safe if already disconnected.

        Also removes the saved connection config, preventing reconnect(). Use
        unregister_client() from BT nodes when you want to preserve reconnect
        capability.
        """
        with self._sessions_lock:
            client = self._sessions.pop(session_id, None)
            self._session_configs.pop(session_id, None)
        if client is None:
            return
        with contextlib.suppress(Exception):  # best-effort
            self.run_sync(self._backend.disconnect(client))

    def register_client(self, session_id: str, client: Any) -> None:
        """Register an already-connected client under a session ID."""
        with self._sessions_lock:
            self._sessions[session_id] = client

    def unregister_client(self, session_id: str) -> Any | None:
        """Remove and return a client without disconnecting it.

        Unlike disconnect(), this intentionally preserves _session_configs so
        that reconnect() remains available after the BT node OpcUaDisconnect
        runs.
        """
        with self._sessions_lock:
            return self._sessions.pop(session_id, None)

    def get_client(self, session_id: str) -> Any:
        """Return the client for a session. Raises SessionNotFoundError if absent."""
        with self._sessions_lock:
            client = self._sessions.get(session_id)
        if client is None:
            raise SessionNotFoundError(
                f"Session '{session_id}' not found. "
                f"Active sessions: {self.active_sessions()}"
            )
        return client

    def is_connected(self, session_id: str) -> bool:
        with self._sessions_lock:
            return session_id in self._sessions

    def active_sessions(self) -> list[str]:
        with self._sessions_lock:
            return list(self._sessions)

    def reconnect(self, session_id: str) -> None:
        """Re-establish a previously connected session using its saved config.

        Raises ConnectionError if no saved config exists or reconnect fails.

        The config is preserved after unregister_client() (used by
        OpcUaDisconnect) but cleared by disconnect() (programmatic disconnect).
        """
        with self._sessions_lock:
            config = self._session_configs.get(session_id)
        if config is None:
            raise ConnectionError(
                f"No saved config for session '{session_id}' — connect first"
            )
        endpoint = config["endpoint"]
        kwargs = {k: v for k, v in config.items() if k != "endpoint"}
        with self._sessions_lock:
            self._sessions.pop(session_id, None)
        self.connect(session_id, endpoint, **kwargs)

    def get_session_config(self, session_id: str) -> dict | None:
        """Return a copy of the saved connection config, or None if not found."""
        with self._sessions_lock:
            cfg = self._session_configs.get(session_id)
        return dict(cfg) if cfg is not None else None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _default_backend(self) -> OpcUaBackend:
        from bteng_opcua.backend.asyncua_backend import AsyncUaBackend
        return AsyncUaBackend()
