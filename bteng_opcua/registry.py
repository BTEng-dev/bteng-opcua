"""Node definition registry — loads YAML/JSON, resolves aliases, validates schema."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bteng_opcua.exceptions import NodeAliasError
from bteng_opcua.types import OpcUaDataType


@dataclass
class ArgDefinition:
    """Single input or output argument of an OPC UA method."""
    name: str
    type: OpcUaDataType

    @classmethod
    def from_str(cls, raw: str) -> ArgDefinition:
        """Parse 'name:type' shorthand (e.g. 'target_x:double')."""
        parts = raw.split(":", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Invalid arg definition {raw!r} — expected 'name:type'"
            )
        name, type_str = parts[0].strip(), parts[1].strip()
        try:
            dtype = OpcUaDataType(type_str)
        except ValueError as exc:
            valid = [t.value for t in OpcUaDataType if t != OpcUaDataType.METHOD]
            raise ValueError(
                f"Unknown type {type_str!r} in arg '{name}'. Valid: {valid}"
            ) from exc
        return cls(name=name, type=dtype)

    @classmethod
    def from_dict(cls, d: dict) -> ArgDefinition:
        """Parse {'name': ..., 'type': ...} mapping."""
        try:
            return cls(name=d["name"], type=OpcUaDataType(d["type"]))
        except KeyError as exc:
            raise ValueError(f"Arg definition missing field {exc}: {d}") from exc
        except ValueError as exc:
            raise ValueError(f"Invalid type in arg {d}: {exc}") from exc


@dataclass
class NodeDefinition:
    """Complete OPC UA node or method definition resolved from an alias."""
    alias:             str
    node_index:        int
    node_id:           str
    data_type:         OpcUaDataType
    object_id:         str | None             = None
    method_id:         str | None             = None
    condition_node_id: str | None             = None
    condition_value:   Any | None             = None
    input_args:        list[ArgDefinition]    = field(default_factory=list)
    output_args:       list[ArgDefinition]    = field(default_factory=list)

    @property
    def is_method(self) -> bool:
        return self.data_type == OpcUaDataType.METHOD

    @property
    def full_node_id(self) -> str:
        """Fully qualified OPC UA node ID string (e.g. 'ns=2;i=1001').

        Raises AttributeError for method nodes — use object_id and method_id instead.
        """
        if self.is_method:
            raise AttributeError(
                f"Node alias '{self.alias}' is a method — it has no node_id. "
                "Use object_id and method_id instead."
            )
        return f"ns={self.node_index};{self.node_id}"

    def validate(self) -> None:
        """Raise ValueError if the definition is internally inconsistent."""
        if self.is_method:
            if not self.object_id:
                raise ValueError(
                    f"Method alias '{self.alias}' missing required 'object_id'"
                )
            if not self.method_id:
                raise ValueError(
                    f"Method alias '{self.alias}' missing required 'method_id'"
                )
        else:
            if not self.node_id:
                raise ValueError(
                    f"Data alias '{self.alias}' missing required 'node_id'"
                )


class NodeRegistry:
    """Registry of OPC UA node definitions loaded from YAML or JSON files."""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeDefinition] = {}

    # ── Loading ───────────────────────────────────────────────────────────────

    def load_file(self, path: str | Path) -> None:
        """Parse a YAML or JSON file and merge its definitions into this registry."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Node definition file not found: {path}")
        text = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(text)
        elif path.suffix == ".json":
            data = json.loads(text)
        else:
            raise ValueError(
                f"Unsupported file format '{path.suffix}'. Use .yaml, .yml, or .json"
            )
        self.load_dict(data)

    def load_dict(self, data: dict) -> None:
        """Merge definitions from a parsed dictionary into this registry."""
        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("Registry data must contain a top-level 'nodes' list")
        for raw in nodes:
            defn = self._parse_node(raw)
            defn.validate()
            self._nodes[defn.alias] = defn

    # ── Lookup ────────────────────────────────────────────────────────────────

    def resolve(self, alias: str) -> NodeDefinition:
        """Return the NodeDefinition for an alias. Raises NodeAliasError if absent."""
        node = self._nodes.get(alias)
        if node is None:
            raise NodeAliasError(
                f"Unknown node alias '{alias}'. "
                f"Registered aliases: {sorted(self._nodes)}"
            )
        return node

    def has(self, alias: str) -> bool:
        return alias in self._nodes

    def all_aliases(self) -> list[str]:
        return list(self._nodes.keys())

    # ── Internal parsing ──────────────────────────────────────────────────────

    def _parse_node(self, raw: dict) -> NodeDefinition:
        for key in ("alias", "node_index", "node_id", "data_type"):
            if key not in raw:
                raise ValueError(
                    f"Node definition missing required field '{key}': {raw}"
                )
        alias = str(raw["alias"])
        try:
            data_type = OpcUaDataType(str(raw["data_type"]))
        except ValueError as exc:
            valid = [t.value for t in OpcUaDataType]
            raise ValueError(
                f"Unknown data_type '{raw['data_type']}' for alias '{alias}'. "
                f"Supported: {valid}"
            ) from exc
        return NodeDefinition(
            alias=alias,
            node_index=int(raw["node_index"]),
            node_id=str(raw["node_id"]),
            data_type=data_type,
            object_id=raw.get("object_id"),
            method_id=raw.get("method_id"),
            condition_node_id=raw.get("condition_node_id"),
            condition_value=raw.get("condition_value"),
            input_args=self._parse_args(raw.get("input_args", [])),
            output_args=self._parse_args(raw.get("output_args", [])),
        )

    def _parse_args(self, raw_args: list) -> list[ArgDefinition]:
        result: list[ArgDefinition] = []
        for item in raw_args:
            if isinstance(item, str):
                result.append(ArgDefinition.from_str(item))
            elif isinstance(item, dict):
                result.append(ArgDefinition.from_dict(item))
            else:
                raise ValueError(f"Invalid arg definition item: {item!r}")
        return result


# ── Global registry singleton ─────────────────────────────────────────────────

_global_registry: NodeRegistry | None = None


def get_global_registry() -> NodeRegistry:
    """Return the process-wide shared NodeRegistry, creating it if needed."""
    global _global_registry
    if _global_registry is None:
        _global_registry = NodeRegistry()
    return _global_registry


def reset_global_registry() -> None:
    """Clear and recreate the global registry (useful in tests)."""
    global _global_registry
    _global_registry = None


def load_global(path: str | Path) -> None:
    """Load node definitions from a file into the global registry."""
    get_global_registry().load_file(path)
