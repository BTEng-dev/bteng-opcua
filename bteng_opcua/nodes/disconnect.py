"""OpcUaDisconnect — close an OPC UA session."""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.session import OpcUaSessionManager

logger = logging.getLogger(__name__)


class OpcUaDisconnect(StatefulActionNode):
    """Disconnect a named OPC UA session.

    Returns SUCCESS immediately if the session was not connected.
    Returns RUNNING while the disconnect is in progress.
    Returns SUCCESS once the disconnect completes (errors are swallowed — the
    session is considered gone regardless of server-side errors).
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._session_id: str = ""

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id", "Session identifier to disconnect", default="default"),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        # Use unregister_client() (not manager.disconnect()) to preserve _session_configs,
        # enabling reconnect() after a BT-level disconnect.
        client = manager.unregister_client(session_id)
        if client is None:
            self.set_feedback_message(f"Session '{session_id}' was not connected")
            logger.debug("session %r was not connected", session_id)
            return NodeStatus.SUCCESS

        self._session_id = session_id
        logger.info("disconnecting session %r", session_id)
        self._future = manager.submit(manager._backend.disconnect(client))
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING
        # swallow errors — disconnect is best-effort
        with contextlib.suppress(Exception):
            self._future.result()
        logger.info("session %r disconnected", self._session_id)
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
