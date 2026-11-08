"""Custom exceptions for bteng_opcua."""
from __future__ import annotations


class OpcUaError(Exception):
    """Base exception for all bteng_opcua errors."""


class ConnectionError(OpcUaError):
    """Failed to connect to or communicate with an OPC UA server."""


class SessionNotFoundError(OpcUaError):
    """Referenced session ID does not exist or is not connected."""


class NodeAliasError(OpcUaError):
    """Node alias not found in the registry or definition is invalid."""


class TypeConversionError(OpcUaError):
    """Cannot convert between Python and OPC UA types."""


class MethodCallError(OpcUaError):
    """OPC UA method call failed."""


class ConditionNotSatisfiedError(OpcUaError):
    """Precondition node value did not match the expected value."""
