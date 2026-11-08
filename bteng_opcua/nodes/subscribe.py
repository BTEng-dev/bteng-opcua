"""OpcUaSubscribe — react to OPC UA data-change notifications."""
from __future__ import annotations

import concurrent.futures
import contextlib
import queue
import time
from typing import Any

from bteng.core.node import InputPort, NodeConfig, NodeStatus, OutputPort, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.registry import NodeDefinition, get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, to_python


class OpcUaSubscribe(StatefulActionNode):
    """Subscribe to an OPC UA data-change notification.

    Returns RUNNING while waiting.
    Returns SUCCESS when:
      mode=on_change — any notification received.
      mode=on_value  — received value == expected_value.
    Returns FAILURE on session missing, subscription error, or timeout.

    Stores the received value in the blackboard using output_key (or the
    node alias) and via the 'value' output port if remapped.
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._setup_future: concurrent.futures.Future | None = None
        self._sub_handle: Any | None = None
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._deadline: float | None = None
        self._node_id: str = ""
        self._node_def: NodeDefinition | None = None
        self._persistent: bool = False

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("session_id",     "OPC UA session identifier",                   default="default"),
            InputPort("node_alias",     "Registry alias for the variable node",        default=""),
            InputPort("node_id",        "OPC UA node ID override (ns=X;...)",          default=""),
            InputPort("node_index",     "Namespace index override",                    default=0),
            InputPort("data_type",      "Data type override (if not using alias)",     default=""),
            InputPort("mode",           "on_change — any change; on_value — match expected_value", default="on_change"),
            InputPort("expected_value", "Required value when mode=on_value",           default=None),
            InputPort("period_ms",      "Subscription publish interval in ms",         default=500),
            InputPort("timeout",        "Max wait seconds (0 = infinite)",             default=0.0),
            InputPort("output_key",     "Blackboard key to store received value",      default=""),
            InputPort("persistent",     "Keep subscription alive across multiple activations", default="false"),
            OutputPort("value",         "The value received from the subscription"),
        ]

    # ── StatefulActionNode hooks ──────────────────────────────────────────────

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
        self._node_id = node_id

        self._persistent = str(self.get_input("persistent", "false")).lower() in ("true", "1", "yes")

        if self._persistent and self._sub_handle is not None:
            # Reuse existing subscription — just reset deadline and fresh queue
            timeout = float(self.get_input("timeout", 0.0))
            self._deadline = time.monotonic() + timeout if timeout > 0 else None
            self._queue = queue.SimpleQueue()   # fresh queue; discard mid-idle notifications
            return NodeStatus.RUNNING

        timeout = float(self.get_input("timeout", 0.0))
        self._deadline = time.monotonic() + timeout if timeout > 0 else None
        self._queue = queue.SimpleQueue()
        self._sub_handle = None

        period_ms = int(self.get_input("period_ms", 500))

        def _on_change(value: Any) -> None:
            self._queue.put_nowait(value)

        self._setup_future = manager.submit(
            manager._backend.create_subscription(client, node_id, _on_change, period_ms)
        )
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        # Phase 1 — wait for subscription setup
        if self._sub_handle is None:
            if not self._setup_future.done():
                return self._check_timeout()
            try:
                self._sub_handle = self._setup_future.result()
            except Exception as exc:
                self.set_feedback_message(f"Subscription setup failed: {exc}")
                return NodeStatus.FAILURE

        # Phase 2 — wait for notification
        try:
            value = self._queue.get_nowait()
        except queue.Empty:
            return self._check_timeout()

        mode = self.get_input("mode", "on_change")

        if mode == "on_value":
            expected = self.get_input("expected_value")
            if self._coerce(value) != self._coerce(expected):
                # Wrong value — discard and keep waiting for next notification
                return self._check_timeout()

        converted = self._convert(value)
        self._store(converted)
        self.set_feedback_message(f"Received {converted!r} from {self._node_id}")
        if not self._persistent:
            self._cleanup_sub()
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        # Setup future may have completed before on_running stored the handle
        if (self._sub_handle is None
                and self._setup_future is not None
                and self._setup_future.done()):
            with contextlib.suppress(Exception):
                self._sub_handle = self._setup_future.result()
        self._cleanup_sub()
        if self._setup_future is not None and not self._setup_future.done():
            self._setup_future.cancel()
        self._setup_future = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_timeout(self) -> NodeStatus:
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self.set_feedback_message(
                f"Subscription timed out waiting on {self._node_id}"
            )
            if self._setup_future is not None and not self._setup_future.done():
                self._setup_future.cancel()
            self._setup_future = None
            self._cleanup_sub()
            return NodeStatus.FAILURE
        return NodeStatus.RUNNING

    def _cleanup_sub(self) -> None:
        if self._sub_handle is not None:
            manager = OpcUaSessionManager.get_instance()
            fut = manager.submit(manager._backend.delete_subscription(self._sub_handle))

            def _log_error(f):
                exc = f.exception()
                if exc is not None:
                    self.set_feedback_message(f"delete_subscription error: {exc}")

            fut.add_done_callback(_log_error)
            self._sub_handle = None

    def _resolve_node(self) -> tuple[str | None, NodeDefinition | None]:
        alias = self.get_input("node_alias", "")
        if alias:
            registry = self._config.params.get("registry") or get_global_registry()
            try:
                defn = registry.resolve(alias)
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

    def _convert(self, raw: Any) -> Any:
        if self._node_def is not None:
            with contextlib.suppress(Exception):
                return to_python(raw, self._node_def.data_type)
        type_str = self.get_input("data_type", "")
        if type_str:
            with contextlib.suppress(Exception):
                return to_python(raw, OpcUaDataType(type_str))
        return raw

    def _coerce(self, v: Any) -> Any:
        """Best-effort type coercion for on_value comparison."""
        if self._node_def is not None:
            with contextlib.suppress(Exception):
                return to_python(v, self._node_def.data_type)
        type_str = self.get_input("data_type", "")
        if type_str:
            with contextlib.suppress(Exception):
                return to_python(v, OpcUaDataType(type_str))
        return v

    def _store(self, value: Any) -> None:
        if not self.set_output("value", value):
            output_key = self.get_input("output_key", "")
            key = output_key or (self._node_def.alias if self._node_def else "opcua_value")
            if self._config.blackboard is not None:
                self._config.blackboard.set(key, value, writer=self.uid)
