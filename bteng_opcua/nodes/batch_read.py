"""OpcUaBatchRead — read multiple OPC UA nodes in a single round-trip."""
from __future__ import annotations

import concurrent.futures
import contextlib

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, to_python


class OpcUaBatchRead(StatefulActionNode):
    """Read multiple OPC UA nodes in a single request and store results on the blackboard.

    ``node_ids`` — comma-separated list of full OPC UA node IDs (e.g. ``ns=2;s=Temp,ns=2;s=Pressure``).
    ``output_keys`` — comma-separated list of blackboard keys, one per node ID.
    ``data_types`` — optional comma-separated list of type names (float, int32, …).

    The lists must have the same length.  Missing ``data_types`` entries leave
    values unconverted (raw OPC UA Python type).
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._output_keys: list[str] = []
        self._data_types: list[OpcUaDataType | None] = []

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",   "OPC UA session identifier",                       default="default"),
            InputPort("node_ids",     "Comma-separated OPC UA node IDs",                 default=""),
            InputPort("node_aliases", "Comma-separated registry aliases (alternative to node_ids)", default=""),
            InputPort("output_keys",  "Comma-separated blackboard keys (one per node)",  default=""),
            InputPort("data_types",   "Comma-separated type names for conversion",        default=""),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_aliases_raw = str(self.get_input("node_aliases", "")).strip()
        if node_aliases_raw:
            aliases = [a.strip() for a in node_aliases_raw.split(",") if a.strip()]
            registry = self._config.params.get("registry") or get_global_registry()
            node_ids = []
            resolved_types: list[OpcUaDataType | None] = []
            for alias in aliases:
                try:
                    defn = registry.resolve(alias)
                except Exception as exc:
                    self.set_feedback_message(str(exc))
                    return NodeStatus.FAILURE
                if defn.is_method:
                    self.set_feedback_message(
                        f"Alias '{alias}' is a method node — can't batch read"
                    )
                    return NodeStatus.FAILURE
                node_ids.append(defn.full_node_id)
                resolved_types.append(defn.data_type)

            output_keys_raw = str(self.get_input("output_keys", "")).strip()
            self._output_keys = (
                [s.strip() for s in output_keys_raw.split(",") if s.strip()]
                if output_keys_raw
                else aliases
            )
            if len(self._output_keys) != len(node_ids):
                self.set_feedback_message(
                    f"output_keys ({len(self._output_keys)}) and node_aliases ({len(node_ids)}) lengths differ"
                )
                return NodeStatus.FAILURE

            data_types_raw = str(self.get_input("data_types", "")).strip()
            if data_types_raw:
                type_strs = [s.strip() for s in data_types_raw.split(",") if s.strip()]
                if len(type_strs) != len(node_ids):
                    self.set_feedback_message(
                        f"data_types ({len(type_strs)}) and node_aliases ({len(node_ids)}) lengths differ"
                    )
                    return NodeStatus.FAILURE
                self._data_types = []
                for ts in type_strs:
                    try:
                        self._data_types.append(OpcUaDataType(ts))
                    except ValueError:
                        self.set_feedback_message(f"Unknown data_type: '{ts}'")
                        return NodeStatus.FAILURE
            else:
                self._data_types = resolved_types

            self._future = manager.submit(manager._backend.read_nodes(client, node_ids))
            return NodeStatus.RUNNING

        node_ids_raw = str(self.get_input("node_ids", "")).strip()
        output_keys_raw = str(self.get_input("output_keys", "")).strip()

        if not node_ids_raw or not output_keys_raw:
            self.set_feedback_message("'node_ids' and 'output_keys' are required")
            return NodeStatus.FAILURE

        node_ids = [s.strip() for s in node_ids_raw.split(",") if s.strip()]
        self._output_keys = [s.strip() for s in output_keys_raw.split(",") if s.strip()]

        if len(node_ids) != len(self._output_keys):
            self.set_feedback_message(
                f"node_ids ({len(node_ids)}) and output_keys ({len(self._output_keys)}) lengths differ"
            )
            return NodeStatus.FAILURE

        data_types_raw = str(self.get_input("data_types", "")).strip()
        if data_types_raw:
            type_strs = [s.strip() for s in data_types_raw.split(",") if s.strip()]
            if len(type_strs) != len(node_ids):
                self.set_feedback_message(
                    f"data_types has {len(type_strs)} entries but node_ids has {len(node_ids)}"
                )
                return NodeStatus.FAILURE
        else:
            type_strs = [""] * len(node_ids)

        self._data_types = []
        for ts in type_strs:
            if ts:
                try:
                    self._data_types.append(OpcUaDataType(ts))
                except ValueError:
                    self.set_feedback_message(f"Unknown data_type: '{ts}'")
                    return NodeStatus.FAILURE
            else:
                self._data_types.append(None)

        self._future = manager.submit(manager._backend.read_nodes(client, node_ids))
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            values = self._future.result()
        except Exception as exc:
            self.set_feedback_message(f"Batch read failed: {exc}")
            return NodeStatus.FAILURE

        bb = self._config.blackboard
        for key, raw, dtype in zip(self._output_keys, values, self._data_types, strict=False):
            if dtype is not None:
                with contextlib.suppress(Exception):
                    raw = to_python(raw, dtype)
            if bb is not None:
                bb.set(key, raw, writer=self.uid)

        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
