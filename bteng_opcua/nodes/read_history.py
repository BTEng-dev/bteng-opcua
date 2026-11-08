"""OpcUaReadHistory — read historical values from an OPC UA variable node."""
from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.session import OpcUaSessionManager


class OpcUaReadHistory(StatefulActionNode):
    """Read historical data values from an OPC UA HistoricalAccess node.

    Results are stored on the blackboard as a list of ``(datetime, value)`` tuples
    under ``output_key``.

    Time window is expressed as ``end_time`` (ISO-8601 UTC string or "now") minus
    ``duration_seconds``.  Both default to the past minute.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",       "OPC UA session identifier",                        default="default"),
            InputPort("node_id",          "Full OPC UA node ID (ns=X;…)",                     default=""),
            InputPort("output_key",       "Blackboard key for the result list",               default="history"),
            InputPort("duration_seconds", "Length of the history window in seconds",           default=60.0),
            InputPort("end_time",         "End of history window: ISO-8601 UTC or 'now'",     default="now"),
            InputPort("max_values",       "Maximum number of data points to return",           default=100),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_id = str(self.get_input("node_id", "")).strip()
        if not node_id:
            self.set_feedback_message("'node_id' is required")
            return NodeStatus.FAILURE

        end_raw = str(self.get_input("end_time", "now")).strip()
        if end_raw == "now":
            end_dt = datetime.now(tz=UTC)
        else:
            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=UTC)
            except ValueError as exc:
                self.set_feedback_message(f"Invalid end_time: {exc}")
                return NodeStatus.FAILURE

        duration = float(self.get_input("duration_seconds", 60.0))
        start_dt = end_dt - timedelta(seconds=duration)
        max_values = int(self.get_input("max_values", 100))

        self._future = manager.submit(
            manager._backend.read_history(client, node_id, start_dt, end_dt, max_values)
        )
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            history = self._future.result()
        except Exception as exc:
            self.set_feedback_message(f"Read history failed: {exc}")
            return NodeStatus.FAILURE

        output_key = str(self.get_input("output_key", "history"))
        bb = self._config.blackboard
        if bb is not None:
            bb.set(output_key, history, writer=self.uid)

        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
