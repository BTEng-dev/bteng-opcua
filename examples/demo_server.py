"""A small OPC UA server to demo `bteng-opcua harvest` and `bteng-opcua run` against.

Exposes an information model under Objects/Robot:

    Temperature   Float    (read-only)
    RobotReady    Boolean  (read-only)
    Setpoint      Double   (writable)
    MoveToPosition(target_x: Double, target_y: Double, speed: Float)
                  -> (result_code: Int32, reached_x: Double, reached_y: Double)

Run it in one terminal, then harvest and run a tree in another:

    python examples/demo_server.py
    bteng-opcua harvest -s examples/config/harvest.yaml -o examples/config/nodes.yaml
    bteng-opcua run examples/trees/ -n examples/config/nodes.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from typing import Any

from asyncua import Server, ua, uamethod

ENDPOINT = "opc.tcp://127.0.0.1:4840/bteng/"
NAMESPACE = "http://bteng.example/robot"


def _argument(name: str, type_id: int, description: str = "") -> ua.Argument:
    """A named method argument — this is what `harvest` reads back."""
    arg = ua.Argument()
    arg.Name = name
    arg.DataType = ua.NodeId(type_id)
    arg.ValueRank = -1
    arg.ArrayDimensions = []
    arg.Description = ua.LocalizedText(description)
    return arg


@uamethod
def move_to_position(parent, target_x: float, target_y: float, speed: float):
    """Pretend to move, then report where we ended up.

    Return a *tuple* of Variants: asyncua maps a tuple onto separate output
    arguments, whereas a list would be serialized as one array argument.
    """
    return (
        ua.Variant(0, ua.VariantType.Int32),
        ua.Variant(float(target_x), ua.VariantType.Double),
        ua.Variant(float(target_y), ua.VariantType.Double),
    )


async def build_model(server: Server) -> tuple[int, Any]:
    """Create the Robot information model. Returns (namespace index, Temperature)."""
    idx = await server.register_namespace(NAMESPACE)

    robot = await server.nodes.objects.add_object(idx, "Robot")

    temperature = await robot.add_variable(
        idx, "Temperature", 21.5, ua.VariantType.Float
    )
    await robot.add_variable(idx, "RobotReady", True, ua.VariantType.Boolean)
    setpoint = await robot.add_variable(idx, "Setpoint", 0.0, ua.VariantType.Double)
    await setpoint.set_writable()

    await robot.add_method(
        idx,
        "MoveToPosition",
        move_to_position,
        [
            _argument("target_x", ua.ObjectIds.Double, "target X in metres"),
            _argument("target_y", ua.ObjectIds.Double, "target Y in metres"),
            _argument("speed", ua.ObjectIds.Float, "speed 0..1"),
        ],
        [
            _argument("result_code", ua.ObjectIds.Int32, "0 = OK"),
            _argument("reached_x", ua.ObjectIds.Double),
            _argument("reached_y", ua.ObjectIds.Double),
        ],
    )
    return idx, temperature


async def export_nodeset(path: str) -> None:
    """Write the model out as a NodeSet2.xml — the offline input to `harvest`."""
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    idx, _ = await build_model(server)
    # export_values=True is required, otherwise the InputArguments/OutputArguments
    # properties are written without their <Value>, and the method's signature
    # is lost from the file.
    await server.export_xml_by_ns(path, namespaces=[idx], export_values=True)
    print(f"Exported NodeSet2 to {path} (namespace {idx} = {NAMESPACE})")


async def serve() -> None:
    server = Server()
    await server.init()
    server.set_endpoint(ENDPOINT)
    server.set_server_name("BTEng OPC UA demo server")
    idx, temperature = await build_model(server)

    print(f"Serving on {ENDPOINT} (namespace {idx} = {NAMESPACE})")
    async with server:
        temp = 21.5
        while True:
            await asyncio.sleep(1.0)
            temp += 0.1
            await temperature.write_value(ua.Variant(temp, ua.VariantType.Float))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--export", metavar="PATH",
                    help="Write the model as a NodeSet2.xml and exit (no serving)")
    args = ap.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(export_nodeset(args.export) if args.export else serve())
