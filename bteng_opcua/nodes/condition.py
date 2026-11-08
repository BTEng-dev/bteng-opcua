"""OpcUaCondition — check an OPC UA node value against an expected value."""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, to_python

logger = logging.getLogger(__name__)


class OpcUaCondition(StatefulActionNode):
    """Read an OPC UA node and compare its value to an expected value.

    Returns SUCCESS when ``current_value == expected_value`` (after type conversion).
    Returns FAILURE otherwise, or if the read fails.

    Comparison is done on the Python-converted value, so string ``"True"``
    matches boolean ``True`` when ``data_type="boolean"``.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._data_type: OpcUaDataType | None = None

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",      "OPC UA session identifier",             default="default"),
            InputPort("node_alias",      "Registry alias for the variable node",  default=""),
            InputPort("node_id",         "OPC UA node ID override (ns=X;…)",      default=""),
            InputPort("node_index",      "Namespace index (only with node_id)",   default=0),
            InputPort("data_type",       "Python type for value conversion",       default=""),
            InputPort("expected_value",  "Expected value (string comparison OK)", default=""),
        ]

    def on_start(self) -> NodeStatus:
        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_id, self._data_type = self._resolve_node()
        if node_id is None:
            return NodeStatus.FAILURE

        self._future = manager.submit(manager._backend.read_node(client, node_id))
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            raw = self._future.result()
        except Exception as exc:
            self.set_feedback_message(f"Read failed: {exc}")
            logger.warning("condition read failed: %s", exc)
            return NodeStatus.FAILURE

        value = self._convert(raw)
        expected = self._coerce_expected(value)

        if value == expected:
            logger.info("condition met: %r == %r", value, expected)
            return NodeStatus.SUCCESS

        self.set_feedback_message(
            f"Condition not met: got {value!r}, expected {expected!r}"
        )
        logger.info("condition not met: got %r, expected %r", value, expected)
        return NodeStatus.FAILURE

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_node(self) -> tuple[str | None, OpcUaDataType | None]:
        alias = str(self.get_input("node_alias", "")).strip()
        if alias:
            registry = self._config.params.get("registry") or get_global_registry()
            try:
                defn = registry.resolve(alias)
                if defn.is_method:
                    self.set_feedback_message(
                        f"Alias '{alias}' is a method node — use OpcUaCallMethod instead"
                    )
                    return None, None
                return defn.full_node_id, defn.data_type
            except Exception as exc:
                self.set_feedback_message(str(exc))
                return None, None

        node_id = str(self.get_input("node_id", "")).strip()
        if not node_id:
            self.set_feedback_message("Provide 'node_alias' or 'node_id'")
            return None, None
        node_index = int(self.get_input("node_index", 0))
        full_id = f"ns={node_index};{node_id}" if not node_id.startswith("ns=") else node_id

        type_str = str(self.get_input("data_type", "")).strip()
        dtype = None
        if type_str:
            try:
                dtype = OpcUaDataType(type_str)
            except ValueError:
                self.set_feedback_message(f"Unknown data_type: '{type_str}'")
                return None, None
        return full_id, dtype

    def _convert(self, raw: object) -> object:
        if self._data_type is not None:
            with contextlib.suppress(Exception):
                return to_python(raw, self._data_type)
        return raw

    def _coerce_expected(self, converted_value: object) -> object:
        raw_str = str(self.get_input("expected_value", ""))
        if isinstance(converted_value, bool):
            return raw_str.lower() in ("true", "1", "yes")
        if isinstance(converted_value, int):
            try:
                return int(raw_str)
            except (ValueError, TypeError):
                pass
        if isinstance(converted_value, float):
            try:
                return float(raw_str)
            except (ValueError, TypeError):
                pass
        return raw_str
