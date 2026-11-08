"""OpcUaBatchWrite — write multiple OPC UA variable nodes in a single BT tick."""
from __future__ import annotations

import asyncio
import concurrent.futures

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, get_variant_type, to_python


async def _write_all(backend, client, writes):
    """Run all writes in parallel via asyncio.gather.

    writes: list of (node_id, typed_value, variant_type)
    """
    await asyncio.gather(*(
        backend.write_node(client, nid, val, vt)
        for nid, val, vt in writes
    ))


class OpcUaBatchWrite(StatefulActionNode):
    """Write multiple OPC UA variable nodes in a single BT tick using parallel async writes.

    ``node_ids``   — comma-separated list of full OPC UA node IDs (e.g. ``ns=2;s=Temp,ns=2;s=Pressure``).
    ``input_keys`` — comma-separated blackboard keys to read write-values from, one per node ID.
    ``data_types`` — comma-separated type names for conversion (required — no raw-value mode).

    All three lists must have the same length.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",  "OPC UA session identifier",                              default="default"),
            InputPort("node_ids",    "Comma-separated OPC UA node IDs",                        default=""),
            InputPort("input_keys",  "Comma-separated blackboard keys (one per node)",         default=""),
            InputPort("data_types",  "Comma-separated type names for conversion (required)",   default=""),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_ids_raw   = str(self.get_input("node_ids",   "")).strip()
        input_keys_raw = str(self.get_input("input_keys", "")).strip()
        data_types_raw = str(self.get_input("data_types", "")).strip()

        if not node_ids_raw or not input_keys_raw:
            self.set_feedback_message("'node_ids' and 'input_keys' are required")
            return NodeStatus.FAILURE

        # data_types is required for batch write (no raw-value mode)
        if not data_types_raw:
            self.set_feedback_message("'data_types' is required for OpcUaBatchWrite")
            return NodeStatus.FAILURE

        node_ids   = [s.strip() for s in node_ids_raw.split(",")   if s.strip()]
        input_keys = [s.strip() for s in input_keys_raw.split(",") if s.strip()]
        type_strs  = [s.strip() for s in data_types_raw.split(",") if s.strip()]

        if len(node_ids) != len(input_keys):
            self.set_feedback_message(
                f"node_ids ({len(node_ids)}) and input_keys ({len(input_keys)}) lengths differ"
            )
            return NodeStatus.FAILURE

        if len(type_strs) != len(node_ids):
            self.set_feedback_message(
                f"data_types has {len(type_strs)} entries but node_ids has {len(node_ids)}"
            )
            return NodeStatus.FAILURE

        # Resolve types and read values from blackboard
        bb = self._config.blackboard
        writes: list[tuple] = []  # (node_id, typed_value, variant_type)

        for nid, key, ts in zip(node_ids, input_keys, type_strs, strict=False):
            # Parse data type
            try:
                dtype = OpcUaDataType(ts)
            except ValueError:
                self.set_feedback_message(f"Unknown data_type: '{ts}'")
                return NodeStatus.FAILURE

            # Read from blackboard
            if bb is None or not bb.has(key):
                self.set_feedback_message(
                    f"Blackboard key '{key}' not found"
                )
                return NodeStatus.FAILURE

            raw = bb.get(key)

            # Convert type
            try:
                typed_value  = to_python(raw, dtype)
                variant_type = get_variant_type(dtype)
            except Exception as exc:
                self.set_feedback_message(f"Type conversion failed for '{key}': {exc}")
                return NodeStatus.FAILURE

            writes.append((nid, typed_value, variant_type))

        self._future = manager.submit(_write_all(manager._backend, client, writes))
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING
        try:
            self._future.result()
            return NodeStatus.SUCCESS
        except Exception as exc:
            self.set_feedback_message(f"Batch write failed: {exc}")
            return NodeStatus.FAILURE

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
