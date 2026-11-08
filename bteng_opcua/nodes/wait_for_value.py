"""OpcUaWaitForValue — poll an OPC UA node until its value matches expected or timeout fires."""
from __future__ import annotations

import concurrent.futures
import contextlib
import time

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import NodeDefinition, get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, to_python


class OpcUaWaitForValue(StatefulActionNode):
    """Poll an OPC UA variable node until its value matches ``expected_value`` or a timeout fires.

    Unlike ``OpcUaSubscribe``, this uses repeated synchronous reads, making it
    suitable for servers that do not support subscriptions.

    The node returns:

    * ``SUCCESS`` — the read value equals ``expected_value`` (after type coercion).
    * ``FAILURE`` — session missing, node resolution failed, read error, or timeout.
    * ``RUNNING`` — still polling.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._next_poll: float = 0.0
        self._deadline: float | None = None
        self._node_id: str = ""
        self._node_def: NodeDefinition | None = None
        self._session_id: str = ""

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",      "OPC UA session identifier",              default="default"),
            InputPort("node_alias",      "Registry alias for the variable node",   default=""),
            InputPort("node_id",         "OPC UA node ID override (ns=X;...)",     default=""),
            InputPort("node_index",      "Namespace index (only with node_id)",    default=0),
            InputPort("data_type",       "Data type override (if not using alias)", default=""),
            InputPort("expected_value",  "Value to wait for (string-coerced)",     default=""),
            InputPort("output_key",      "Blackboard key to store matched value",  default=""),
            InputPort("poll_interval",   "Seconds between reads",                  default=0.5),
            InputPort("timeout",         "Max wait in seconds; 0 = infinite",      default=0.0),
        ]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_start(self) -> NodeStatus:
        self._session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()

        try:
            manager.get_client(self._session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        node_id, self._node_def = self._resolve_node()
        if node_id is None:
            return NodeStatus.FAILURE

        self._node_id = node_id

        timeout = float(self.get_input("timeout", 0.0))
        self._deadline = time.monotonic() + timeout if timeout > 0.0 else None
        self._next_poll = time.monotonic()  # poll immediately on first tick
        self._future = None
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        # Phase 1: collect result if a future is done
        if self._future is not None and self._future.done():
            try:
                raw = self._future.result()
            except Exception as exc:
                self.set_feedback_message(f"Read failed: {exc}")
                return NodeStatus.FAILURE

            self._future = None
            value = self._convert(raw)
            expected = self._coerce_expected(value)

            if value == expected:
                self._store(value)
                self.set_feedback_message(f"Value matched: {value!r}")
                return NodeStatus.SUCCESS

            # Wrong value — schedule next poll
            poll_interval = float(self.get_input("poll_interval", 0.5))
            self._next_poll = time.monotonic() + poll_interval

        # Phase 2: submit next read if it's time and no read is in flight
        if self._future is None and time.monotonic() >= self._next_poll:
            manager = OpcUaSessionManager.get_instance()
            try:
                client = manager.get_client(self._session_id)
            except Exception as exc:
                self.set_feedback_message(str(exc))
                return NodeStatus.FAILURE
            self._future = manager.submit(manager._backend.read_node(client, self._node_id))

        return self._check_timeout()

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_node(self) -> tuple[str | None, NodeDefinition | None]:
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
                return defn.full_node_id, defn
            except Exception as exc:
                self.set_feedback_message(str(exc))
                return None, None

        node_id = str(self.get_input("node_id", "")).strip()
        if not node_id:
            self.set_feedback_message("Provide 'node_alias' or 'node_id'")
            return None, None

        node_index = int(self.get_input("node_index", 0))
        full_id = f"ns={node_index};{node_id}" if not node_id.startswith("ns=") else node_id
        return full_id, None

    def _convert(self, raw: object) -> object:
        if self._node_def is not None:
            with contextlib.suppress(Exception):
                return to_python(raw, self._node_def.data_type)
        type_str = str(self.get_input("data_type", "")).strip()
        if type_str:
            with contextlib.suppress(Exception):
                dtype = OpcUaDataType(type_str)
                return to_python(raw, dtype)
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

    def _store(self, value: object) -> None:
        output_key = str(self.get_input("output_key", "")).strip()
        key = output_key or (self._node_def.alias if self._node_def else "opcua_value")
        if self._config.blackboard is not None:
            self._config.blackboard.set(key, value, writer=self.uid)

    def _check_timeout(self) -> NodeStatus:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.set_feedback_message(f"Timed out waiting for value on {self._node_id}")
            return NodeStatus.FAILURE
        return NodeStatus.RUNNING
