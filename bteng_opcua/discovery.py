"""Harvest OPC UA node definitions from a live server or a NodeSet2.xml file.

The OPC UA information model already carries everything ``nodes.yaml`` needs:
every Variable exposes ``DataType`` and ``ValueRank`` attributes, and every
Method exposes ``InputArguments``/``OutputArguments`` properties listing each
argument's name and type.  This module reads those and emits the registry YAML,
so the addresses and types never have to be transcribed by hand.

Two sources, one output:

* :func:`harvest_from_server`  — browse a running server over ``opc.tcp://``
* :func:`harvest_from_nodeset` — parse a NodeSet2.xml file, fully offline

A server that offers no Anonymous identity token needs credentials to browse;
pass them as an ``auth:`` section in the spec or via :class:`HarvestAuth`.

Both return ``(definitions, warnings)``, where *definitions* is a list of dicts
in ``NodeRegistry`` schema and *warnings* lists every node that was skipped.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bteng_opcua.types import OpcUaDataType

# ── OPC UA built-in DataType NodeIds (namespace 0) → registry data types ──────
# Ref: OPC UA Part 6, Table A.1.  Types with no counterpart in OpcUaDataType
# (SByte i=2, Int16 i=4, Guid i=14, ByteString i=15, ...) are deliberately absent
# and cause the node to be skipped with a warning rather than mis-typed.
_BUILTIN_TYPES: dict[int, OpcUaDataType] = {
    1:  OpcUaDataType.BOOLEAN,
    3:  OpcUaDataType.BYTE,
    5:  OpcUaDataType.UINT16,
    6:  OpcUaDataType.INT32,
    7:  OpcUaDataType.UINT32,
    8:  OpcUaDataType.INT64,
    9:  OpcUaDataType.UINT64,
    10: OpcUaDataType.FLOAT,
    11: OpcUaDataType.DOUBLE,
    12: OpcUaDataType.STRING,
    13: OpcUaDataType.STRING,  # DateTime — read as an ISO string
    21: OpcUaDataType.LOCALIZED_TEXT,
}

# NodeSet2.xml writes DataType as an alias name ("Double") or a NodeId ("i=11").
_BUILTIN_NAMES: dict[str, OpcUaDataType] = {
    "Boolean":  OpcUaDataType.BOOLEAN,
    "Byte":     OpcUaDataType.BYTE,
    "UInt16":   OpcUaDataType.UINT16,
    "Int32":    OpcUaDataType.INT32,
    "UInt32":   OpcUaDataType.UINT32,
    "Int64":    OpcUaDataType.INT64,
    "UInt64":   OpcUaDataType.UINT64,
    "Float":    OpcUaDataType.FLOAT,
    "Double":   OpcUaDataType.DOUBLE,
    "String":   OpcUaDataType.STRING,
    "DateTime": OpcUaDataType.STRING,
    "LocalizedText": OpcUaDataType.LOCALIZED_TEXT,
}

_ARRAY_OF: dict[OpcUaDataType, OpcUaDataType] = {
    OpcUaDataType.BOOLEAN: OpcUaDataType.BOOLEAN_ARRAY,
    OpcUaDataType.BYTE:    OpcUaDataType.BYTE_ARRAY,
    OpcUaDataType.UINT16:  OpcUaDataType.UINT16_ARRAY,
    OpcUaDataType.INT32:   OpcUaDataType.INT32_ARRAY,
    OpcUaDataType.UINT32:  OpcUaDataType.UINT32_ARRAY,
    OpcUaDataType.INT64:   OpcUaDataType.INT64_ARRAY,
    OpcUaDataType.UINT64:  OpcUaDataType.UINT64_ARRAY,
    OpcUaDataType.FLOAT:   OpcUaDataType.FLOAT_ARRAY,
    OpcUaDataType.DOUBLE:  OpcUaDataType.DOUBLE_ARRAY,
    OpcUaDataType.STRING:  OpcUaDataType.STRING_ARRAY,
}

_UANODESET_NS = "{http://opcfoundation.org/UA/2011/03/UANodeSet.xsd}"
_UATYPES_NS = "{http://opcfoundation.org/UA/2008/02/Types.xsd}"

# A NodeSet2 file holds only its own namespace, so the standard folders a browse
# path starts from ("Objects/Robot") are referenced but never defined in it.
_STANDARD_ROOTS: dict[str, str] = {
    "Root": "i=84",
    "Objects": "i=85",
    "Types": "i=86",
    "Views": "i=87",
}


@dataclass
class HarvestRoot:
    """One entry of the spec's ``harvest:`` list.

    Either a browse path ("Objects/Robot") or an explicit node_id.  When the
    root resolves to an Object it is walked recursively; a Variable or Method
    root is emitted on its own.
    """
    path: str | None = None
    node_id: str | None = None
    alias: str | None = None
    recursive: bool = True

    @classmethod
    def parse(cls, raw: str | dict) -> HarvestRoot:
        if isinstance(raw, str):
            return cls(node_id=raw) if _looks_like_node_id(raw) else cls(path=raw)
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid harvest entry: {raw!r}")
        root = cls(
            path=raw.get("path") or raw.get("browse"),
            node_id=raw.get("node_id"),
            alias=raw.get("alias"),
            recursive=bool(raw.get("recursive", True)),
        )
        if not root.path and not root.node_id:
            raise ValueError(f"Harvest entry needs 'path' or 'node_id': {raw!r}")
        return root


@dataclass
class HarvestAuth:
    """Credentials for browsing a server that rejects anonymous sessions.

    Mirrors the parameters :class:`~bteng_opcua.nodes.connect.OpcUaConnect`
    accepts, so one set of credentials covers both harvesting and running.
    """
    username: str | None = None
    password: str | None = None
    certificate_path: str | None = None
    private_key_path: str | None = None

    def __bool__(self) -> bool:
        return bool(self.username or self.certificate_path)

    @classmethod
    def from_dict(cls, data: dict | None) -> HarvestAuth:
        data = data or {}
        return cls(
            username=data.get("username") or None,
            password=data.get("password") or None,
            certificate_path=data.get("certificate_path") or None,
            private_key_path=data.get("private_key_path") or None,
        )

    def merged_with(self, **overrides: str | None) -> HarvestAuth:
        """Return a copy with any non-None override applied (CLI over spec)."""
        return HarvestAuth(**{
            f: (overrides.get(f) if overrides.get(f) is not None else getattr(self, f))
            for f in ("username", "password", "certificate_path", "private_key_path")
        })


@dataclass
class HarvestSpec:
    """Parsed harvest spec file."""
    roots: list[HarvestRoot] = field(default_factory=list)
    server: str | None = None
    nodeset: str | None = None
    auth: HarvestAuth = field(default_factory=HarvestAuth)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> HarvestSpec:
        raw_roots = data.get("harvest")
        if not isinstance(raw_roots, list) or not raw_roots:
            raise ValueError("Spec must contain a non-empty top-level 'harvest' list")
        known = {"harvest", "server", "nodeset", "auth"}
        return cls(
            roots=[HarvestRoot.parse(r) for r in raw_roots],
            server=data.get("server"),
            nodeset=data.get("nodeset"),
            auth=HarvestAuth.from_dict(data.get("auth")),
            extra={k: v for k, v in data.items() if k not in known},
        )


def _looks_like_node_id(raw: str) -> bool:
    return bool(re.match(r"^(ns=\d+;)?[isgb]=", raw))


def snake_case(name: str) -> str:
    """Robot.MoveToPosition → move_to_position."""
    name = name.split(":")[-1].split(".")[-1]
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return name.strip("_").lower() or "node"


def _apply_rank(dtype: OpcUaDataType, value_rank: int) -> OpcUaDataType | None:
    """ValueRank >= 1 means an array; -1/-2/-3 are treated as scalar."""
    if value_rank is not None and value_rank >= 1:
        return _ARRAY_OF.get(dtype)
    return dtype


class _AliasBook:
    """Assigns unique aliases, disambiguating collisions with the parent name."""

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def assign(self, browse_name: str, parent: str | None = None,
               override: str | None = None) -> str:
        if override:
            base = override
        else:
            base = snake_case(browse_name)
            if base in self._taken and parent:
                base = f"{snake_case(parent)}_{base}"
        alias = base
        n = 2
        while alias in self._taken:
            alias = f"{base}_{n}"
            n += 1
        self._taken.add(alias)
        return alias


# ── Live server ───────────────────────────────────────────────────────────────

async def _harvest_server_async(
    endpoint: str, roots: list[HarvestRoot], timeout: float,
    auth: HarvestAuth | None = None,
) -> tuple[list[dict], list[str]]:
    from asyncua import Client, ua

    defs: list[dict] = []
    warnings: list[str] = []
    book = _AliasBook()

    def ident(nid: Any) -> str:
        """NodeId → 'i=1001' / 's=Temperature' identifier part."""
        s = str(nid.to_string())
        return s.split(";", 1)[1] if ";" in s else s

    def full(nid: Any) -> str:
        return f"ns={nid.NamespaceIndex};{ident(nid)}"

    async def builtin_of(dt_nodeid: Any) -> OpcUaDataType | None:
        """Walk the DataType's supertype chain until a built-in type is reached."""
        node: Any = client.get_node(dt_nodeid)
        for _ in range(12):
            nid = node.nodeid
            ident_num = nid.Identifier
            if (
                nid.NamespaceIndex == 0
                and isinstance(ident_num, int)
                and ident_num in _BUILTIN_TYPES
            ):
                return _BUILTIN_TYPES[ident_num]
            try:
                node = await node.get_parent()
            except Exception:
                return None
            if node is None:
                return None
        return None

    async def args_of(method, prop_name: str) -> list[str] | None:
        for child in await method.get_children():
            bn = await child.read_browse_name()
            if bn.Name != prop_name:
                continue
            out: list[str] = []
            for arg in await child.read_value() or []:
                dtype = await builtin_of(arg.DataType)
                dtype = _apply_rank(dtype, arg.ValueRank) if dtype else None
                if dtype is None:
                    return None  # unmappable arg → skip the whole method
                out.append(f"{snake_case(arg.Name)}:{dtype.value}")
            return out
        return []

    async def emit_variable(node, parent_name, alias_override=None) -> None:
        bn = await node.read_browse_name()
        try:
            dt = await builtin_of(await node.read_data_type())
            rank = await node.read_value_rank()
        except Exception as exc:
            warnings.append(f"{bn.Name}: could not read type attributes ({exc})")
            return
        dtype = _apply_rank(dt, rank) if dt else None
        if dtype is None:
            warnings.append(
                f"{bn.Name} ({full(node.nodeid)}): unsupported DataType/ValueRank — skipped"
            )
            return
        defs.append({
            "alias": book.assign(bn.Name, parent_name, alias_override),
            "node_index": node.nodeid.NamespaceIndex,
            "node_id": ident(node.nodeid),
            "data_type": dtype.value,
        })

    async def emit_method(node, parent, parent_name, alias_override=None) -> None:
        bn = await node.read_browse_name()
        if parent is None:
            warnings.append(f"{bn.Name}: method has no parent object — skipped")
            return
        inputs = await args_of(node, "InputArguments")
        outputs = await args_of(node, "OutputArguments")
        if inputs is None or outputs is None:
            warnings.append(
                f"{bn.Name} ({full(node.nodeid)}): unsupported argument type — skipped"
            )
            return
        defs.append({
            "alias": book.assign(bn.Name, parent_name, alias_override),
            "node_index": node.nodeid.NamespaceIndex,
            "node_id": "",
            "data_type": "method",
            "object_id": full(parent.nodeid),
            "method_id": full(node.nodeid),
            "input_args": inputs,
            "output_args": outputs,
        })

    async def walk(node, parent, parent_name, recursive, alias_override=None) -> None:
        node_class = await node.read_node_class()
        if node_class == ua.NodeClass.Variable:
            await emit_variable(node, parent_name, alias_override)
        elif node_class == ua.NodeClass.Method:
            await emit_method(node, parent, parent_name, alias_override)
        elif node_class == ua.NodeClass.Object and recursive:
            bn = await node.read_browse_name()
            for child in await node.get_children():
                await walk(child, node, bn.Name, recursive)

    async def resolve(root: HarvestRoot) -> Any:
        if root.node_id:
            return client.get_node(root.node_id)
        node: Any = client.nodes.root
        for part in [p for p in (root.path or "").split("/") if p]:
            want_ns, want_name = (
                (int(part.split(":", 1)[0]), part.split(":", 1)[1])
                if ":" in part and part.split(":", 1)[0].isdigit()
                else (None, part)
            )
            for child in await node.get_children():
                bn = await child.read_browse_name()
                if bn.Name == want_name and (want_ns is None or bn.NamespaceIndex == want_ns):
                    node = child
                    break
            else:
                raise LookupError(f"Browse path '{root.path}' — no child named '{part}'")
        return node

    client = Client(url=endpoint, timeout=timeout)
    if auth and auth.username:
        client.set_user(auth.username)
        client.set_password(auth.password or "")
    if auth and auth.certificate_path:
        await client.load_client_certificate(auth.certificate_path)
        if auth.private_key_path:
            await client.load_private_key(auth.private_key_path)

    async with client:
        for root in roots:
            try:
                node = await resolve(root)
                parent = await node.get_parent()
            except Exception as exc:
                warnings.append(f"{root.path or root.node_id}: {exc} — skipped")
                continue
            pname = None
            if parent is not None:
                pname = (await parent.read_browse_name()).Name
            await walk(node, parent, pname, root.recursive, root.alias)

    return defs, warnings


def harvest_from_server(
    endpoint: str, roots: list[HarvestRoot], timeout: float = 10.0,
    auth: HarvestAuth | None = None,
) -> tuple[list[dict], list[str]]:
    """Browse a live OPC UA server and return (definitions, warnings).

    *auth* is required by servers that offer no Anonymous identity token —
    without it they answer ``BadIdentityTokenRejected`` at session activation.
    """
    import asyncio
    return asyncio.run(_harvest_server_async(endpoint, roots, timeout, auth))


# ── NodeSet2.xml (offline) ────────────────────────────────────────────────────

def harvest_from_nodeset(
    path: str | Path, roots: list[HarvestRoot]
) -> tuple[list[dict], list[str]]:
    """Parse a NodeSet2.xml information model and return (definitions, warnings).

    Namespace indices come from the file's own ``<NamespaceUris>`` order.  A
    server may assign different indices to the same URIs, so verify a generated
    file against the real server with ``--check`` before trusting the indices.
    """
    root_el = ET.parse(str(path)).getroot()
    warnings: list[str] = []
    book = _AliasBook()

    aliases = {
        el.get("Alias"): (el.text or "").strip()
        for el in root_el.findall(f"{_UANODESET_NS}Aliases/{_UANODESET_NS}Alias")
    }

    # NodeId → element, for parent lookups and browse-path resolution.
    by_id: dict[str, ET.Element] = {}
    for el in root_el:
        nid = el.get("NodeId")
        if nid and el.tag.startswith(f"{_UANODESET_NS}UA"):
            by_id[nid] = el

    def browse_name(el: ET.Element) -> str:
        return (el.get("BrowseName") or "").split(":", 1)[-1]

    def ns_index(node_id: str) -> int:
        m = re.match(r"ns=(\d+);", node_id or "")
        return int(m.group(1)) if m else 0

    def ident(node_id: str) -> str:
        return node_id.split(";", 1)[1] if ";" in node_id else node_id

    def full(node_id: str) -> str:
        return f"ns={ns_index(node_id)};{ident(node_id)}"

    def resolve_type(raw: str | None, value_rank: str | None) -> OpcUaDataType | None:
        if not raw:
            return None
        dtype = _BUILTIN_NAMES.get(raw)
        if dtype is None:
            target = aliases.get(raw, raw)
            m = re.match(r"^(?:ns=0;)?i=(\d+)$", target)
            dtype = (
                _BUILTIN_TYPES.get(int(m.group(1))) if m else _BUILTIN_NAMES.get(target)
            )
        if dtype is None:
            return None
        return _apply_rank(dtype, int(value_rank) if value_rank else -1)

    def method_args(method_id: str, prop_name: str) -> list[str] | None:
        """Arguments of a method, or None when they cannot be determined.

        A method with genuinely no arguments has no such property and yields [].
        A property that exists but carries no <Value> means the file was exported
        without values — the signature is unknowable, so return None rather than
        pretend the method takes nothing.
        """
        for el in root_el.findall(f"{_UANODESET_NS}UAVariable"):
            if el.get("ParentNodeId") != method_id or browse_name(el) != prop_name:
                continue
            if el.find(f"{_UANODESET_NS}Value") is None:
                return None
            out: list[str] = []
            for arg in el.iter(f"{_UATYPES_NS}Argument"):
                name_el = arg.find(f"{_UATYPES_NS}Name")
                dt_el = arg.find(f"{_UATYPES_NS}DataType/{_UATYPES_NS}Identifier")
                rank_el = arg.find(f"{_UATYPES_NS}ValueRank")
                dtype = resolve_type(
                    (dt_el.text or "").strip() if dt_el is not None else None,
                    (rank_el.text or "").strip() if rank_el is not None else None,
                )
                if dtype is None or name_el is None:
                    return None
                out.append(f"{snake_case(name_el.text or '')}:{dtype.value}")
            return out
        return []

    def path_to_element(path: str) -> ET.Element:
        """Walk BrowseNames down from Objects/Root using ParentNodeId links."""
        parts = [p.split(":", 1)[-1] for p in path.split("/") if p]
        current: str | None = None
        for i, part in enumerate(parts):
            # Standard folders are not defined in the file; resolve them by name.
            if i == 0 and part in _STANDARD_ROOTS and part not in {
                browse_name(el) for el in by_id.values()
            }:
                current = _STANDARD_ROOTS[part]
                continue
            found = None
            for nid, el in by_id.items():
                if browse_name(el) != part:
                    continue
                if current is None or el.get("ParentNodeId") == current:
                    found = nid
                    break
            if found is None:
                raise LookupError(f"Browse path '{path}' — no node named '{part}'")
            current = found
        if current is None or current not in by_id:
            raise LookupError(f"Browse path '{path}' resolves to no node in the file")
        return by_id[current]

    def emit(el: ET.Element, parent_name: str | None, alias_override=None) -> None:
        tag = el.tag[len(_UANODESET_NS):]
        node_id = el.get("NodeId") or ""
        name = browse_name(el)

        if tag == "UAVariable":
            dtype = resolve_type(el.get("DataType"), el.get("ValueRank"))
            if dtype is None:
                warnings.append(
                    f"{name} ({full(node_id)}): unsupported DataType "
                    f"{el.get('DataType')!r} — skipped"
                )
                return
            defs.append({
                "alias": book.assign(name, parent_name, alias_override),
                "node_index": ns_index(node_id),
                "node_id": ident(node_id),
                "data_type": dtype.value,
            })
        elif tag == "UAMethod":
            parent_id = el.get("ParentNodeId")
            inputs = method_args(node_id, "InputArguments")
            outputs = method_args(node_id, "OutputArguments")
            if not parent_id:
                warnings.append(f"{name} ({full(node_id)}): no parent object — skipped")
                return
            if inputs is None or outputs is None:
                warnings.append(
                    f"{name} ({full(node_id)}): argument signature unreadable — the "
                    "NodeSet2 was likely exported without values, or an argument uses "
                    "an unsupported type. Harvest from the live server instead. Skipped."
                )
                return
            defs.append({
                "alias": book.assign(name, parent_name, alias_override),
                "node_index": ns_index(node_id),
                "node_id": "",
                "data_type": "method",
                "object_id": full(parent_id),
                "method_id": full(node_id),
                "input_args": inputs,
                "output_args": outputs,
            })

    def walk(el: ET.Element, parent_name: str | None, recursive: bool,
             alias_override=None) -> None:
        tag = el.tag[len(_UANODESET_NS):]
        if tag in ("UAVariable", "UAMethod"):
            # Argument properties are part of their method, not standalone nodes.
            if browse_name(el) in ("InputArguments", "OutputArguments"):
                return
            emit(el, parent_name, alias_override)
        elif tag == "UAObject" and recursive:
            my_id = el.get("NodeId")
            for child in by_id.values():
                if child.get("ParentNodeId") == my_id:
                    walk(child, browse_name(el), recursive)

    defs: list[dict] = []
    for root in roots:
        try:
            el = by_id[root.node_id] if root.node_id else path_to_element(root.path or "")
        except (KeyError, LookupError) as exc:
            warnings.append(f"{root.path or root.node_id}: {exc} — skipped")
            continue
        parent_el = by_id.get(el.get("ParentNodeId") or "")
        parent_name = browse_name(parent_el) if parent_el is not None else None
        walk(el, parent_name, root.recursive, root.alias)

    return defs, warnings
