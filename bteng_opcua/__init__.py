"""bteng_opcua — OPC UA integration for the BTEng Behavior Tree engine."""
from __future__ import annotations

import logging

from bteng_opcua.backend.asyncua_backend import AsyncUaBackend
from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.exceptions import (
    ConditionNotSatisfiedError,
    ConnectionError,
    MethodCallError,
    NodeAliasError,
    OpcUaError,
    SessionNotFoundError,
    TypeConversionError,
)
from bteng_opcua.nodes.batch_read import OpcUaBatchRead
from bteng_opcua.nodes.batch_write import OpcUaBatchWrite
from bteng_opcua.nodes.call_method import OpcUaCallMethod
from bteng_opcua.nodes.condition import OpcUaCondition
from bteng_opcua.nodes.connect import OpcUaConnect
from bteng_opcua.nodes.disconnect import OpcUaDisconnect
from bteng_opcua.nodes.read import OpcUaRead
from bteng_opcua.nodes.read_history import OpcUaReadHistory
from bteng_opcua.nodes.reconnect import OpcUaReconnect
from bteng_opcua.nodes.sleep import Sleep
from bteng_opcua.nodes.subscribe import OpcUaSubscribe
from bteng_opcua.nodes.wait_for_value import OpcUaWaitForValue
from bteng_opcua.nodes.write import OpcUaWrite
from bteng_opcua.registry import (
    ArgDefinition,
    NodeDefinition,
    NodeRegistry,
    get_global_registry,
    load_global,
    reset_global_registry,
)
from bteng_opcua.session import OpcUaSessionManager
from bteng_opcua.types import OpcUaDataType, get_variant_type, to_opcua_variant, to_python

# Library logging: emit plain records under the "bteng_opcua" logger tree and
# attach a NullHandler so nothing is printed unless the host app configures
# logging.  Colour/emoji formatting is a presentation concern and stays out of
# the library — the consumer's handler decides how these lines look.
logging.getLogger(__name__).addHandler(logging.NullHandler())

load_nodes = load_global

# XML / BTEng factory integration — imported lazily to avoid a hard BTEng
# dependency for users who only need the OPC UA backend/session helpers.
def register_nodes(factory=None):
    """Register all bteng_opcua nodes with BTEng's NodeFactory singleton.

    Delegates to :func:`bteng_opcua.xml_integration.register_nodes`.
    Requires bteng to be installed.
    """
    from bteng_opcua.xml_integration import register_nodes as _reg
    return _reg(factory)


def load_tree(path, tree_id=None, blackboard=None, factory=None):
    """Load a BTEng XML behaviour tree with all bteng_opcua nodes pre-registered.

    Delegates to :func:`bteng_opcua.xml_integration.load_tree`.
    Requires bteng to be installed.
    """
    from bteng_opcua.xml_integration import load_tree as _load
    return _load(path, tree_id=tree_id, blackboard=blackboard, factory=factory)


BTENG_NODES = [
    ("OpcUaConnect",      OpcUaConnect),
    ("OpcUaDisconnect",   OpcUaDisconnect),
    ("OpcUaRead",         OpcUaRead),
    ("OpcUaWrite",        OpcUaWrite),
    ("OpcUaCallMethod",   OpcUaCallMethod),
    ("OpcUaSubscribe",    OpcUaSubscribe),
    ("OpcUaBatchRead",    OpcUaBatchRead),
    ("OpcUaBatchWrite",   OpcUaBatchWrite),
    ("OpcUaReadHistory",  OpcUaReadHistory),
    ("OpcUaCondition",    OpcUaCondition),
    ("OpcUaReconnect",    OpcUaReconnect),
    ("OpcUaWaitForValue", OpcUaWaitForValue),
    ("Sleep",             Sleep),
]

__all__ = [
    # Exceptions
    "OpcUaError",
    "ConnectionError",
    "SessionNotFoundError",
    "NodeAliasError",
    "TypeConversionError",
    "MethodCallError",
    "ConditionNotSatisfiedError",
    # Types
    "OpcUaDataType",
    "to_python",
    "to_opcua_variant",
    "get_variant_type",
    # Registry
    "NodeRegistry",
    "NodeDefinition",
    "ArgDefinition",
    "get_global_registry",
    "reset_global_registry",
    "load_global",
    "load_nodes",
    # Session
    "OpcUaSessionManager",
    # Backend
    "OpcUaBackend",
    "AsyncUaBackend",
    # BT Nodes
    "OpcUaConnect",
    "OpcUaDisconnect",
    "OpcUaRead",
    "OpcUaWrite",
    "OpcUaCallMethod",
    "OpcUaSubscribe",
    "OpcUaBatchRead",
    "OpcUaBatchWrite",
    "OpcUaReadHistory",
    "OpcUaCondition",
    "OpcUaReconnect",
    "OpcUaWaitForValue",
    "Sleep",
    # BTEng plugin integration
    "BTENG_NODES",
    "register_nodes",
    "load_tree",
]
