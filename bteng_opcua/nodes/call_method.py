"""OpcUaCallMethod — invoke an OPC UA method with typed args and output storage."""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging
from typing import Any

from bteng.core.node import InputPort, NodeConfig, NodeStatus, PortDefinition
from bteng.nodes.leaf.stateful_action import StatefulActionNode

from bteng_opcua.exceptions import ConditionNotSatisfiedError
from bteng_opcua.registry import NodeDefinition, get_global_registry
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import to_opcua_variant, to_python

logger = logging.getLogger(__name__)


class OpcUaCallMethod(StatefulActionNode):
    """Invoke an OPC UA method defined in the node registry.

    Execution sequence:
      1. Resolve method alias → NodeDefinition
      2. If ConditionNodeId is set: read the condition node and compare its value
         to ConditionValue.  Return FAILURE if the condition is not satisfied.
      3. Resolve each InputArg from the blackboard (key = arg name, remappable
         via NodeConfig.input_ports).
      4. Convert args to OPC UA Variants and call the method.
      5. Convert output values to Python types and write them to the blackboard
         (key = arg name, remappable via NodeConfig.output_ports; falls back to
         '{output_prefix}{arg_name}' when no port mapping is declared).

    BT status:
      RUNNING  — async call in flight
      SUCCESS  — method returned without error
      FAILURE  — session missing, alias unknown, condition unmet, or call error
    """

    def __init__(self, name: str, config: NodeConfig | None = None) -> None:
        super().__init__(name, config)
        self._future: concurrent.futures.Future | None = None
        self._node_def: NodeDefinition | None = None

    @classmethod
    def provided_ports(cls) -> list[PortDefinition]:
        return [
            InputPort("method_alias",   "Registry alias of the method node", default=""),
            InputPort("session_id",     "OPC UA session identifier",         default="default"),
            InputPort("output_prefix",  "Prefix prepended to output blackboard keys", default=""),
        ]

    # ── StatefulActionNode hooks ──────────────────────────────────────────────

    def on_start(self) -> NodeStatus:
        alias = self.get_input("method_alias", "")
        if not alias:
            self.set_feedback_message("Missing required port 'method_alias'")
            return NodeStatus.FAILURE

        registry = self._config.params.get("registry") or get_global_registry()
        try:
            node_def = registry.resolve(alias)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        if not node_def.is_method:
            self.set_feedback_message(
                f"Alias '{alias}' is a data node (data_type={node_def.data_type}), "
                "not a method"
            )
            return NodeStatus.FAILURE

        session_id = str(self.get_input("session_id", "default"))
        manager = OpcUaSessionManager.get_instance()
        try:
            client = manager.get_client(session_id)
        except Exception as exc:
            self.set_feedback_message(str(exc))
            return NodeStatus.FAILURE

        try:
            input_variants = self._build_input_variants(node_def)
        except Exception as exc:
            self.set_feedback_message(f"Input arg error: {exc}")
            return NodeStatus.FAILURE

        self._node_def = node_def
        self._future = manager.submit(
            self._async_call(manager._backend, client, node_def, input_variants)
        )
        return NodeStatus.RUNNING

    def on_running(self) -> NodeStatus:
        if not self._future.done():
            return NodeStatus.RUNNING

        try:
            results = self._future.result()
        except ConditionNotSatisfiedError as exc:
            self.set_feedback_message(str(exc))
            logger.info("method %r skipped: %s", self._node_def.alias, exc)
            return NodeStatus.FAILURE
        except Exception as exc:
            self.set_feedback_message(f"Method call error: {exc}")
            logger.warning("method %r call error: %s", self._node_def.alias, exc)
            return NodeStatus.FAILURE

        self._store_outputs(results)
        self.set_feedback_message(
            f"Method '{self._node_def.alias}' returned {len(results)} output(s)"
        )
        logger.info("called method %r → %d output(s)", self._node_def.alias, len(results))
        return NodeStatus.SUCCESS

    def on_halted(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
        self._node_def = None

    # ── Async core ────────────────────────────────────────────────────────────

    @staticmethod
    async def _async_call(
        backend: Any,
        client: Any,
        node_def: NodeDefinition,
        input_variants: list[Any],
    ) -> list[Any]:
        if node_def.condition_node_id:
            actual = await backend.read_node(client, node_def.condition_node_id)
            expected = node_def.condition_value
            # Coerce actual to match the expected type when they differ
            if expected is not None and not isinstance(actual, type(expected)):
                with contextlib.suppress(ValueError, TypeError):
                    actual = type(expected)(actual)
            if actual != expected:
                raise ConditionNotSatisfiedError(
                    f"Condition node '{node_def.condition_node_id}' value {actual!r} "
                    f"!= expected {expected!r}"
                )
        results = await backend.call_method(
            client, node_def.object_id, node_def.method_id, input_variants
        )
        return list(results)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_input_variants(self, node_def: NodeDefinition) -> list[Any]:
        variants = []
        for arg in node_def.input_args:
            # Resolution order: port mapping → params → direct blackboard key
            raw = self.get_input(arg.name)
            if raw is None and self._config.blackboard is not None:
                raw = self._config.blackboard.get(arg.name)
            if raw is None:
                raise ValueError(
                    f"Input arg '{arg.name}' for method '{node_def.alias}' is None. "
                    "Set value via blackboard key, input_ports mapping, or params."
                )
            variants.append(to_opcua_variant(raw, arg.type))
        return variants

    def _store_outputs(self, results: list[Any]) -> None:
        if self._node_def is None:
            return
        prefix = self.get_input("output_prefix", "")
        for i, arg in enumerate(self._node_def.output_args):
            if i >= len(results):
                break
            try:
                value = to_python(results[i], arg.type)
            except Exception:
                value = results[i]

            if not self.set_output(arg.name, value):
                key = f"{prefix}{arg.name}"
                if self._config.blackboard is not None:
                    self._config.blackboard.set(key, value, writer=self.uid)
