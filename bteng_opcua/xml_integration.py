"""XML and factory integration for bteng_opcua nodes with BTEng.

This module bridges bteng_opcua's OPC UA BT nodes into BTEng's singleton
``NodeFactory`` and the ``XMLTreeParser``.

BTEng API used
--------------
- ``NodeFactory.get_instance()``   — retrieve the singleton factory/registry
- ``NodeFactory.register(cls, name)`` — register a node class under a name
- ``NodeFactory.is_registered(name)`` — check whether a name is known
- ``XMLTreeParser``                — parse a BTEng XML file into a live tree;
  it automatically looks up custom node types via the factory, so registering
  nodes before parsing is all that is required.

Typical usage::

    import bteng_opcua.xml_integration as bt_opc

    # 1. Register all OPC UA nodes with the global BTEng factory.
    bt_opc.register_nodes()

    # 2. (Optional) Load a behaviour-tree XML that contains OPC UA nodes.
    root_node = bt_opc.load_tree("my_bt.xml")
"""
from __future__ import annotations

from pathlib import Path

from bteng.blackboard.blackboard import Blackboard
from bteng.core.node import TreeNode
from bteng.factory.factory import NodeFactory
from bteng.xml_parser.parser import XMLTreeParser

import bteng_opcua


def register_nodes(factory: NodeFactory | None = None) -> None:
    """Register all bteng_opcua nodes with BTEng's NodeFactory.

    Each entry in ``bteng_opcua.BTENG_NODES`` — a list of
    ``(name: str, cls: type)`` pairs — is registered with the factory under
    the given name.  Calling this function multiple times is safe; re-registering
    an already-registered name simply overwrites the entry with the same class.

    Parameters
    ----------
    factory:
        The ``NodeFactory`` instance to use.  Defaults to the global singleton
        (``NodeFactory.get_instance()``), which is what ``XMLTreeParser`` uses
        when no factory is supplied explicitly.
    """
    f = factory or NodeFactory.get_instance()
    for name, cls in bteng_opcua.BTENG_NODES:
        f.register(cls, name)

    # bteng ships SetBlackboard/CheckBlackboard but its factory does not
    # auto-register them; do it here so a tree using <SetBlackboard> builds
    # without the consumer registering anything by hand.
    try:
        from bteng.nodes.leaf.builtins import CheckBlackboard, SetBlackboard
    except ImportError:  # pragma: no cover - a bteng that lacks these builtins
        pass
    else:
        f.register(SetBlackboard, "SetBlackboard")
        f.register(CheckBlackboard, "CheckBlackboard")


def load_tree(
    path: str | Path,
    tree_id: str | None = None,
    blackboard: Blackboard | None = None,
    factory: NodeFactory | None = None,
) -> TreeNode:
    """Load a BTEng XML behaviour tree with all bteng_opcua nodes pre-registered.

    This is a convenience wrapper that:

    1. Calls :func:`register_nodes` so OPC UA node types are available.
    2. Parses the XML file with :class:`bteng.xml_parser.parser.XMLTreeParser`.

    Parameters
    ----------
    path:
        Path to the ``.xml`` file in BTEng XML format.
    tree_id:
        If the file contains multiple ``<Tree ID="...">`` elements, specify
        which one to build.  Defaults to the first (or the one marked by
        ``main_tree_to_execute``).
    blackboard:
        An existing ``Blackboard`` instance to use.  A fresh one is created
        when omitted.
    factory:
        The ``NodeFactory`` instance to register nodes into and to pass to the
        parser.  Defaults to the global singleton.

    Returns
    -------
    TreeNode
        The root node of the parsed behaviour tree.
    """
    f = factory or NodeFactory.get_instance()
    register_nodes(f)
    parser = XMLTreeParser(factory=f)
    return parser.parse_file(str(path), tree_id=tree_id, blackboard=blackboard)
