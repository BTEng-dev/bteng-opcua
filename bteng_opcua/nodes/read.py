"""OpcUaRead — read a value from an OPC UA variable node."""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging

from bteng.core.node import InputPort, NodeConfig, NodeStatus, OutputPort, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import NodeDefinition, get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, to_python

logger = logging.getLogger(__name__)


class OpcUaRead(StatefulActionNode):
    """Read a value from an OPC UA variable node and write it to the blackboard.

    Resolves the node from a registry alias (recommended) or from explicit
    node_index + node_id ports.  The raw OPC UA value is converted to the
    Python type declared by the node definition (or left as-is for raw mode).

    Output key resolution:
      1. output_ports["value"] mapping in NodeConfig (port remapping)
      2. Fallback: blackboard key == value of the 'output_key' port
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._node_def: NodeDefinition | None = None
        self._label: str = ""

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",  "OPC UA session identifier",              default="default"),
            InputPort("node_alias",  "Registry alias for the variable node",   default=""),
            InputPort("node_id",     "OPC UA node ID override (ns=X;...)",     default=""),
            InputPort("node_index",  "Namespace index override",                default=0),
            InputPort("data_type",   "Data type override (if not using alias)", default=""),
            InputPort("output_key",  "Blackboard key to store the value",      default=""),
            OutputPort("value",      "The value read from the OPC UA server"),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_id, self._node_def = self._resolve_node()
        if node_id is None:
            return NodeStatus.FAILURE

        self._label = self._node_def.alias if self._node_def else node_id
        self._future = manager.submit(manager._backend.read_node(client, node_id))
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            raw = self._future.result()
        except Exception as exc:
            self.set_feedback_message(f"Read failed: {exc}")
            logger.warning("read %s failed: %s", self._label, exc)
            return NodeStatus.FAILURE

        value = self._convert(raw)
        self._store(value)
        logger.info("read %s = %r", self._label, value)
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_node(self) -> tuple[str | None, NodeDefinition | None]:
        alias = self.get_input("node_alias", "")
        if alias:
            registry = self._config.params.get("registry") or get_global_registry()
            try:
                defn = registry.resolve(alias)
                if defn.is_method:
                    self.set_feedback_message(
                        f"Alias '{alias}' is a method node — use OpcUaCallMethod instead"
                    )
                    return None, None
                return defn.full_node_id, defn
            except Exception as exc:
                self.set_feedback_message(str(exc))
                return None, None

        node_id = self.get_input("node_id", "")
        node_index = int(self.get_input("node_index", 0))
        if not node_id:
            self.set_feedback_message("Provide 'node_alias' or 'node_id'")
            return None, None
        full_id = f"ns={node_index};{node_id}" if not node_id.startswith("ns=") else node_id
        return full_id, None

    def _convert(self, raw: object) -> object:
        if self._node_def is not None:
            with contextlib.suppress(Exception):
                return to_python(raw, self._node_def.data_type)
        type_str = self.get_input("data_type", "")
        if type_str:
            with contextlib.suppress(Exception):
                dtype = OpcUaDataType(type_str)
                return to_python(raw, dtype)
        return raw

    def _store(self, value: object) -> None:
        if not self.set_output("value", value):
            output_key = self.get_input("output_key", "")
            key = output_key or (self._node_def.alias if self._node_def else "opcua_value")
            if self._config.blackboard is not None:
                self._config.blackboard.set(key, value, writer=self.uid)
