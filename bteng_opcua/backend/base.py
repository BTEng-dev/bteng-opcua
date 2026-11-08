"""Abstract backend interface for OPC UA protocol implementations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Any


class OpcUaBackend(ABC):
    """Pluggable OPC UA I/O layer.

    All methods are async coroutines.  Implement this class to swap the
    default asyncua backend for a mock, stub, or alternative library.
    """

    @abstractmethod
    async def connect(self, endpoint: str, **kwargs) -> Any:
        """Establish a connection. Returns an opaque client handle."""

    @abstractmethod
    async def disconnect(self, client: Any) -> None:
        """Gracefully close the connection."""

    @abstractmethod
    async def read_node(self, client: Any, node_id: str) -> Any:
        """Read the current value of an OPC UA node.

        node_id must be a fully-qualified string: 'ns=X;i=Y' or 'ns=X;s=Name'.
        Returns the raw Python value (OPC UA type unwrapped by asyncua).
        """

    @abstractmethod
    async def write_node(
        self,
        client: Any,
        node_id: str,
        value: Any,
        variant_type: Any,
    ) -> None:
        """Write a typed value to an OPC UA node.

        variant_type is an asyncua VariantType enum member from
        bteng_opcua.types.get_variant_type().
        """

    @abstractmethod
    async def call_method(
        self,
        client: Any,
        object_id: str,
        method_id: str,
        args: list[Any],
    ) -> list[Any]:
        """Invoke an OPC UA method and return a list of output values.

        object_id: fully-qualified NodeId of the parent object.
        method_id: fully-qualified NodeId of the method node.
        args: list of asyncua Variant values (pre-typed by the caller).
        Returns a (possibly empty) list of raw output values.
        """

    @abstractmethod
    async def create_subscription(
        self,
        client: Any,
        node_id: str,
        on_change: Callable[[Any], None],
        period_ms: int = 500,
    ) -> Any:
        """Set up a monitored-item subscription.
        on_change is a plain callable(value) called from asyncua's thread.
        Returns an opaque handle passed to delete_subscription().
        """

    @abstractmethod
    async def delete_subscription(self, handle: Any) -> None:
        """Cancel and delete the subscription identified by handle."""

    @abstractmethod
    async def read_nodes(self, client: Any, node_ids: list[str]) -> list[Any]:
        """Read multiple OPC UA nodes in a single request.
        Returns values in the same order as node_ids."""

    @abstractmethod
    async def read_history(
        self,
        client: Any,
        node_id: str,
        start_time: datetime,
        end_time: datetime,
        max_values: int = 100,
    ) -> list[tuple]:
        """Read historical data for a node.
        Returns list of (datetime, value) tuples ordered oldest-first."""
