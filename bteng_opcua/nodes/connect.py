"""OpcUaConnect — establish an OPC UA session."""
from __future__ import annotations

import concurrent.futures
import logging

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.session import OpcUaSessionManager

logger = logging.getLogger(__name__)


class OpcUaConnect(StatefulActionNode):
    """Connect to an OPC UA server and register a named session.

    Returns SUCCESS immediately if the session is already connected.
    Returns RUNNING while the connection handshake is in progress.
    Returns FAILURE if the connection cannot be established.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._pending_session_id: str = ""

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("endpoint",         "OPC UA server URL (e.g. opc.tcp://localhost:4840)"),
            InputPort("session_id",       "Unique session identifier for reuse",   default="default"),
            InputPort("timeout",          "Connection timeout in seconds",          default=10.0),
            InputPort("username",         "OPC UA username for authentication",     default=""),
            InputPort("password",         "OPC UA password for authentication",     default=""),
            InputPort("certificate_path", "Path to client certificate (.pem/.der)", default=""),
            InputPort("private_key_path", "Path to client private key",             default=""),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        if manager.is_connected(session_id):
            self.set_feedback_message(f"Session '{session_id}' already connected")
            logger.debug("session %r already connected", session_id)
            return NodeStatus.SUCCESS

        endpoint = self.get_input("endpoint")
        if not endpoint:
            self.set_feedback_message("Missing required port 'endpoint'")
            return NodeStatus.FAILURE

        logger.info("connecting session %r to %s", session_id, endpoint)

        timeout = float(self.get_input("timeout", 10.0))
        username = self.get_input("username", "") or None
        password = self.get_input("password", "") or None
        cert     = self.get_input("certificate_path", "") or None
        key      = self.get_input("private_key_path", "") or None

        self._pending_session_id = session_id
        self._future = manager.submit(
            manager._backend.connect(
                endpoint,
                timeout=timeout,
                username=username,
                password=password,
                certificate_path=cert,
                private_key_path=key,
            )
        )
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            client = self._future.result()
            OpcUaSessionManager.get_instance().register_client(
                self._pending_session_id, client
            )
            self.set_feedback_message(f"Session '{self._pending_session_id}' connected")
            logger.info("session %r connected", self._pending_session_id)
            return NodeStatus.SUCCESS
        except Exception as exc:
            self.set_feedback_message(str(exc))
            logger.warning("session %r connect failed: %s", self._pending_session_id, exc)
            return NodeStatus.FAILURE

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
