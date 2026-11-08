"""OpcUaWrite — write a value to an OPC UA variable node."""
from __future__ import annotations

import concurrent.futures
import logging

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import NodeDefinition, get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, get_variant_type, to_python

logger = logging.getLogger(__name__)


class OpcUaWrite(StatefulActionNode):
    """Write a typed value to an OPC UA variable node.

    The value is read from the 'value' input port (static or blackboard-mapped).
    Type conversion is applied using the node definition's declared data_type.
    Returns SUCCESS when the write is acknowledged, FAILURE otherwise.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._label: str = ""
        self._value: object = None

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id", "OPC UA session identifier",              default="default"),
            InputPort("node_alias", "Registry alias for the variable node",   default=""),
            InputPort("node_id",    "OPC UA node ID override (ns=X;...)",     default=""),
            InputPort("node_index", "Namespace index override",                default=0),
            InputPort("data_type",  "Data type override (if not using alias)", default=""),
            InputPort("value",      "Value to write (from blackboard or param)"),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_id, data_type = self._resolve_node()
        if node_id is None or data_type is None:
            return NodeStatus.FAILURE

        raw_value = self.get_input("value")
        if raw_value is None:
            self.set_feedback_message("Missing required port 'value'")
            return NodeStatus.FAILURE

        try:
            typed_value = to_python(raw_value, data_type)
            variant_type = get_variant_type(data_type)
        except Exception as exc:
            self.set_feedback_message(f"Type conversion failed: {exc}")
            return NodeStatus.FAILURE

        self._label = self.get_input("node_alias", "") or node_id
        self._value = typed_value
        self._future = manager.submit(
            manager._backend.write_node(client, node_id, typed_value, variant_type)
        )
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING
        try:
            self._future.result()
            logger.info("wrote %s = %r", self._label, self._value)
            return NodeStatus.SUCCESS
        except Exception as exc:
            self.set_feedback_message(f"Write failed: {exc}")
            logger.warning("write %s failed: %s", self._label, exc)
            return NodeStatus.FAILURE

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_node(self) -> tuple[str | None, OpcUaDataType | None]:
        alias = self.get_input("node_alias", "")
        if alias:
            registry = self._config.params.get("registry") or get_global_registry()
            try:
                defn: NodeDefinition = registry.resolve(alias)
                if defn.is_method:
                    self.set_feedback_message(
                        f"Alias '{alias}' is a method node — use OpcUaCallMethod instead"
                    )
                    return None, None
                return defn.full_node_id, defn.data_type
            except Exception as exc:
                self.set_feedback_message(str(exc))
                return None, None

        node_id = self.get_input("node_id", "")
        node_index = int(self.get_input("node_index", 0))
        type_str = self.get_input("data_type", "")

        if not node_id:
            self.set_feedback_message("Provide 'node_alias' or 'node_id' + 'data_type'")
            return None, None
        if not type_str:
            self.set_feedback_message("Provide 'data_type' when using raw 'node_id'")
            return None, None

        try:
            dtype = OpcUaDataType(type_str)
        except ValueError:
            self.set_feedback_message(f"Unknown data_type: '{type_str}'")
            return None, None

        full_id = f"ns={node_index};{node_id}" if not node_id.startswith("ns=") else node_id
        return full_id, dtype
