"""asyncua-based OPC UA backend."""
from __future__ import annotations

import contextlib
import re
from typing import Any

from bteng_opcua.backend.base import OpcUaBackend
from bteng_opcua.exceptions import ConnectionError, MethodCallError

_NODE_ID_RE = re.compile(r"^(ns=\d+;)?[isgb]=")


def _as_method_id(method_id: Any) -> Any:
    """Convert a node-ID string into a real NodeId.

    asyncua's ``call_method`` treats a *str* method id as a BrowseName and looks
    it up among the object's children, so passing "ns=2;i=5" raises BadNoMatch.
    Browse-name forms ("2:MoveToPosition") are left alone.
    """
    if isinstance(method_id, str) and _NODE_ID_RE.match(method_id):
        from asyncua import ua

        return ua.NodeId.from_string(method_id)
    return method_id


class AsyncUaBackend(OpcUaBackend):
    """Production OPC UA backend using the asyncua library."""

    async def connect(
        self,
        endpoint: str,
        timeout: float = 10.0,
        username: str | None = None,
        password: str | None = None,
        certificate_path: str | None = None,
        private_key_path: str | None = None,
        **kwargs,
    ) -> Any:
        try:
            from asyncua import Client
        except ImportError as exc:
            raise ImportError(
                "asyncua is required — install with: pip install asyncua"
            ) from exc

        client = Client(endpoint, timeout=timeout)
        if username is not None:
            client.set_user(username)
            client.set_password(password or "")
        if certificate_path is not None:
            await client.load_client_certificate(certificate_path)
            if private_key_path:
                await client.load_private_key(private_key_path)
        try:
            await client.connect()
        except Exception as exc:
            raise ConnectionError(f"asyncua connect to {endpoint!r} failed: {exc}") from exc
        return client

    async def disconnect(self, client: Any) -> None:
        with contextlib.suppress(Exception):  # best-effort
            await client.disconnect()

    async def read_node(self, client: Any, node_id: str) -> Any:
        node = client.get_node(node_id)
        return await node.read_value()

    async def write_node(
        self,
        client: Any,
        node_id: str,
        value: Any,
        variant_type: Any,
    ) -> None:
        from asyncua import ua
        node = client.get_node(node_id)
        dv = ua.DataValue(ua.Variant(value, variant_type))
        await node.write_value(dv)

    async def call_method(
        self,
        client: Any,
        object_id: str,
        method_id: str,
        args: list[Any],
    ) -> list[Any]:
        obj_node = client.get_node(object_id)
        try:
            result = await obj_node.call_method(_as_method_id(method_id), *args)
        except Exception as exc:
            raise MethodCallError(
                f"OPC UA call_method({method_id!r}) on object {object_id!r} failed: {exc}"
            ) from exc

        if result is None:
            return []
        if not isinstance(result, (list, tuple)):
            return [result]
        return list(result)

    async def create_subscription(self, client, node_id, on_change, period_ms=500):
        class _Handler:
            def datachange_notification(self_, node, val, data):
                on_change(val)

        sub = await client.create_subscription(period_ms, _Handler())
        node = client.get_node(node_id)
        handle = await sub.subscribe_data_change(node)
        return (sub, handle)

    async def delete_subscription(self, handle):
        sub, h = handle
        with contextlib.suppress(Exception):
            await sub.unsubscribe(h)
            await sub.delete()

    async def read_nodes(self, client: Any, node_ids: list[str]) -> list[Any]:
        nodes = [client.get_node(nid) for nid in node_ids]
        return list(await client.read_values(nodes))

    async def read_history(self, client, node_id, start_time, end_time, max_values=100):
        node = client.get_node(node_id)
        results = await node.read_raw_history(
            starttime=start_time,
            endtime=end_time,
            numvalues=max_values,
        )
        out = []
        for dv in results:
            if dv.Value is not None:
                ts = dv.SourceTimestamp or dv.ServerTimestamp
                out.append((ts, dv.Value.Value))
        return out
