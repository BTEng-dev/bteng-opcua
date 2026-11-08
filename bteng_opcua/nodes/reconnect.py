"""OpcUaReconnect — re-establish a dropped OPC UA session."""
from __future__ import annotations

import concurrent.futures

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.session import OpcUaSessionManager


class OpcUaReconnect(StatefulActionNode):
    """Re-establish a dropped OPC UA session using its previously saved connection config.

    The config is saved by OpcUaConnect (via manager.connect / register_client).
    Unlike OpcUaConnect, this node does *not* require the endpoint port — it reads
    the saved config from the session manager.

    Returns FAILURE immediately if no config is found for the session.
    Returns RUNNING while the connection handshake is in progress.
    Returns SUCCESS once the session is re-registered.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._pending_session_id: str = ""

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id", "Unique session identifier to reconnect", default="default"),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        config = manager.get_session_config(session_id)
        if config is None:
            self.set_feedback_message(
                f"No saved config for session '{session_id}' — connect first"
            )
            return NodeStatus.FAILURE

        # Drop any stale client entry without clearing _session_configs
        manager.unregister_client(session_id)

        endpoint = config["endpoint"]
        kwargs = {k: v for k, v in config.items() if k != "endpoint"}

        self._pending_session_id = session_id
        self._future = manager.submit(
            manager._backend.connect(endpoint, **kwargs)
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
            self.set_feedback_message(
                f"Session '{self._pending_session_id}' reconnected"
            )
            return NodeStatus.SUCCESS
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
