# Reference

Details behind the main [README](../README.md). For a step-by-step walkthrough, see the [authoring guide](authoring-nodes.md).

## Harvest

### Authentication

A server that offers no Anonymous identity token answers `BadIdentityTokenRejected` at session activation, so harvesting needs credentials just like running does:

```yaml
auth:
  username: operator
  password: operator                    # or certificate_path / private_key_path
```

`--username`, `--password`, `--certificate` and `--private-key` override the spec, so the committed file need not hold a secret:

```bash
bteng-opcua harvest -s harvest.yaml -o nodes.yaml --password "$OPCUA_PASSWORD"
```

### Skipped nodes

Nodes whose DataType has no registry equivalent are skipped with a warning rather than mis-typed. `--strict` turns that into a non-zero exit.

### Preserved sections

Sections other than `nodes:` are preserved across regeneration, so a `blackboard:` section (seed values, including method input arguments) that `bteng-opcua run` reads can live in the same file.

### Data type mapping and namespace indices

Built-in OPC UA types map onto the registry types below, and `ValueRank >= 1` selects the array variant. `DateTime` is read as an ISO string. `LocalizedText` becomes `localized_text`. Types with no equivalent (`SByte`, `Int16`, `Guid`, `ByteString`, enums, `NodeId`, and so on) are skipped, including arrays of `LocalizedText`, since reducing a list of localised strings to one `Text` each loses more than it keeps.

When harvesting from a **NodeSet2.xml**, namespace indices come from that file's own `<NamespaceUris>` order, which a server is free to assign differently. Verify a generated file against the real server with `--check` before trusting its indices.

`examples/demo_server.py --export models/robot.NodeSet2.xml` writes the demo model out as a NodeSet2 file, to try the offline harvest path.

## Node registry

The generated file, or one you write yourself, has this shape:

```yaml
nodes:
  - alias: "temperature"
    node_index: 2
    node_id: "s=Temperature"
    data_type: "float"

  - alias: "move_to_home"
    node_index: 2
    node_id: ""
    data_type: "method"
    object_id: "ns=2;i=100"
    method_id: "ns=2;i=101"
    input_args:
      - "speed:float"
    output_args:
      - "result_code:int32"
```

```python
import bteng_opcua
bteng_opcua.load_nodes("nodes.yaml")
```

Required fields are `alias`, `node_index`, `node_id` and `data_type`. Method nodes additionally require `object_id` and `method_id`. Arguments use the shorthand `name:type` string or a `{name, type}` dict. Multiple files can be loaded, and later definitions with the same alias overwrite earlier ones.

## Data types

Valid values for all `data_type` fields:

`int32` `int64` `uint16` `uint32` `uint64` `byte` `float` `double` `string` `boolean` `localized_text`

and their array variants: `int32[]` `float[]` `double[]` `string[]` `boolean[]` and so on. `localized_text` is scalar only.

`localized_text` covers the `LocalizedText` nodes that DI and Machinery models are full of: `ComponentName`, `Lock.CurrentState`, and every state machine's `CurrentState`. It reads as the `Text` field, so the blackboard receives `"DummyModule"` rather than a `LocalizedText` wrapper, and writes back as a `ua.LocalizedText`. The `Locale` is not preserved.

## Session management

Sessions are identified by a `session_id` string (default: `"default"`). Multiple sessions can be active simultaneously against different servers.

- `OpcUaConnect` establishes the session and stores its config.
- `OpcUaDisconnect` removes the live client but preserves the config.
- `OpcUaReconnect` uses the preserved config to reconnect without supplying the endpoint again.

All nodes share a singleton `OpcUaSessionManager` that runs a background asyncio event loop.

## XML and BTEng factory integration

```python
import bteng_opcua

# Register all nodes with BTEng's NodeFactory
bteng_opcua.register_nodes()

# Or load a behaviour tree directly from XML (auto-registers)
tree = bteng_opcua.load_tree("my_tree.xml")
```

### Tree with a method call

```xml
<BTEng format_version="1.0" main_tree_to_execute="main">
  <Tree ID="main">
    <Sequence name="root">
      <OpcUaConnect name="connect" endpoint="opc.tcp://localhost:4840" session_id="main"/>
      <OpcUaRead name="read_temp" session_id="main" node_alias="temperature"
                 value="{current_temp}"/>
      <OpcUaCallMethod name="move" session_id="main" method_alias="move_to_position"
                       output_prefix="move_"/>
      <OpcUaDisconnect name="disconnect" session_id="main"/>
    </Sequence>
  </Tree>

  <!-- Required for value="{key}" to WRITE to the blackboard. -->
  <TreeNodesModel>
    <Action ID="OpcUaRead"><output_port name="value"/></Action>
  </TreeNodesModel>
</BTEng>
```

### Ports and blackboard keys

`attr="{key}"` binds a port to a blackboard key, letting one node's output feed another's input. Declare output ports in `<TreeNodesModel>`: without it the parser treats every `{key}` attribute as an input, and `OpcUaRead` silently falls back to storing under its alias instead. Alternatively, skip the remapping and use the `output_key` port.

Method input arguments are resolved from the blackboard by argument name. Outputs are stored under `output_prefix` plus the argument name.
