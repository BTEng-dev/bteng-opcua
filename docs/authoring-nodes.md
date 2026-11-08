# From node info to a running scenario

The whole point of `bteng_opcua` is that **you don't write integration code**.
You describe *which nodes* on an OPC UA server you care about, and the tool reads
every address, data type and method signature straight out of the server's
information model. A behaviour tree then drives those nodes by name.

So the workflow is:

```
   describe nodes            generate                  run the scenario
  ┌───────────────┐       ┌─────────────┐            ┌──────────────────┐
  │ harvest.yaml  │  ──▶  │  nodes.yaml │  ──▶  XML  │  bteng-opcua run │
  │ (you write)   │       │ (generated) │    tree +  │  (no code)       │
  └───────────────┘       └─────────────┘   registry └──────────────────┘
```

`harvest.yaml` is the **only** file you write by hand, and it is tiny — an
endpoint and a list of nodes. Everything else is generated or declarative XML.

This guide covers the full loop: one-time setup, describing nodes, generating
the registry, adding nodes by hand when you need to, writing a tree, and running
it.

---

## 0. One-time setup

`bteng_opcua` is a normal Python package — install it into a virtual
environment and the `bteng-opcua` command lands on your `PATH`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

pip install bteng-opcua          # from PyPI
# — or, from a checkout of this repo:
pip install -e ".[dev]"          # editable + test tools
```

Either install pulls in the `bteng` engine, `asyncua` and `pyyaml`
automatically. Requirement: **Python ≥ 3.10**. Verify it worked:

```bash
bteng-opcua harvest --help
```

In a real **project** (as opposed to this library) you would name
`bteng-opcua` in your `requirements.txt` and keep this venv-and-install step in
a small `setup.sh`/`Makefile` next to your `config/` and `trees/` — the
bootstrap belongs with the scenario, not with the library. The
[`examples/`](../examples/) folder shows the project layout it applies to.

---

## 1. Describe the nodes you want — `harvest.yaml`

The spec has four possible top-level keys. Only `harvest` is required, and you
need either `server` or `nodeset` to say where the model lives.

```yaml
# harvest.yaml — the only file you write by hand.
server: opc.tcp://192.168.1.10:4840        # a live server …
# nodeset: models/robot.NodeSet2.xml       # … or an offline NodeSet2.xml export

auth:                                       # only if the server rejects anonymous
  username: operator
  password: operator

harvest:
  - "Objects/Robot"                         # a subtree: every Variable + Method under it
  - node_id: "ns=2;i=1001"                  # a single node, alias named yourself
    alias: robot_ready
```

### The `harvest:` list

Each entry names a starting point in the address space. Two forms:

| Form | Example | Meaning |
|---|---|---|
| **Browse path** (string) | `"Objects/Robot"` | Walk this object recursively; emit every Variable and Method under it. Path segments may carry a namespace prefix: `"Objects/2:Robot"`. |
| **Node id** (string) | `"ns=2;i=1001"` or `"i=85"` | A single node. Recognised when it matches `ns=<n>;` followed by `i=`/`s=`/`g=`/`b=`. |
| **Dict** | see below | A node with options. |

The dict form gives you control:

```yaml
harvest:
  - path: "Objects/Robot"     # (or: browse:)  subtree root
    recursive: true           # default true; set false to take only the object's own vars
  - node_id: "ns=2;i=1001"
    alias: robot_ready        # override the auto-generated alias
```

**Aliases** default to the snake-cased BrowseName — `MoveToPosition` becomes
`move_to_position`, `Temperature` becomes `temperature`. On a collision the tool
prefixes the parent name, then falls back to a numeric suffix.

### The `auth:` block

Needed only when the server offers no *Anonymous* identity token — otherwise the
server answers `BadIdentityTokenRejected` when the harvest tries to open a
session. Keys:

```yaml
auth:
  username: operator
  password: operator
  # or, for certificate auth:
  certificate_path: client-cert.pem
  private_key_path: client-key.pem
```

Keep secrets out of the committed file by passing them on the command line
instead (see next section) — they override whatever the spec holds.

---

## 2. Generate the registry — `bteng-opcua harvest`

```bash
bteng-opcua harvest -s harvest.yaml -o nodes.yaml
```

This reads the model and writes `nodes.yaml`, the registry your trees run
against. **Commit the generated file** — trees run fully offline against it; the
server is contacted only when you regenerate.

### Command reference

| Flag | Default | Purpose |
|---|---|---|
| `-s`, `--spec` | *(required)* | The `harvest.yaml` spec. |
| `-o`, `--output` | `nodes.yaml` | Registry file to write. |
| `--server` | *(from spec)* | Override the spec's `opc.tcp://` endpoint. |
| `--nodeset` | *(from spec)* | Override the spec's NodeSet2.xml path. |
| `--from-server` | off | Browse the live server even when a `nodeset:` is configured. |
| `--username` / `--password` | *(from spec)* | Override `auth.username` / `auth.password`. |
| `--certificate` / `--private-key` | *(from spec)* | Override the cert auth paths. |
| `--timeout` | `10.0` | Server connection timeout, seconds. |
| `--check` | off | Do **not** write; exit non-zero if `nodes.yaml` is out of date (CI drift gate). |
| `--strict` | off | Exit non-zero if any node was skipped. |

Keep the secret out of git by passing it at run time:

```bash
bteng-opcua harvest -s harvest.yaml -o nodes.yaml --password "$OPCUA_PASSWORD"
```

### Drift checking in CI

```bash
bteng-opcua harvest -s harvest.yaml -o nodes.yaml --check
```

`--check` re-reads the model and compares it to the committed file. Exit codes:

| Code | Meaning |
|---|---|
| `0` | Up to date. |
| `1` | Stale (a diff is printed) or the output file is missing. |
| `2` | Nothing harvested, or `--strict` was set and nodes were skipped. |

Wire this into CI so a tree can never silently drift from the server's model.

### What gets skipped

Types with no registry equivalent — `SByte`, `Int16`, `Guid`, `ByteString`,
enums, `NodeId`, and arrays of `LocalizedText` — are **skipped with a warning**,
never mis-typed. Add `--strict` to turn any skip into a non-zero exit. See the
data-type table at the bottom for what *is* supported.

---

## 3. Adding a node by hand

You will not usually write registry entries by hand — that is what harvest is
for. But when you have no server to browse, or you want to add a node the model
does not expose cleanly, the registry format is simple. `nodes.yaml` looks like
this:

```yaml
nodes:
  # A plain variable node
  - alias: "temperature"
    node_index: 2
    node_id: "s=Temperature"
    data_type: "float"

  # A method node
  - alias: "move_to_home"
    node_index: 2
    node_id: ""                 # methods carry no variable node_id
    data_type: "method"
    object_id: "ns=2;i=100"     # the object that owns the method
    method_id: "ns=2;i=101"     # the method node itself
    input_args:
      - "speed:float"           # name:type shorthand
      - "target:double"
    output_args:
      - "result_code:int32"
```

**Field rules:**

| Field | Required for | Notes |
|---|---|---|
| `alias` | every node | The name your tree references. |
| `node_index` | every node | Namespace index. |
| `node_id` | every node | The id string; **empty** (`""`) for methods. |
| `data_type` | every node | One of the values in the table below, or `method`. |
| `object_id` | methods | The owning object's node id. |
| `method_id` | methods | The method's node id. |
| `input_args` / `output_args` | methods (optional) | List of `"name:type"` strings or `{name:, type:}` dicts. |
| `condition_node_id` / `condition_value` | optional | Guard a method call on another node's value. |

To add a node, append an entry and reload. Multiple registry files can be
loaded; a later definition with the same alias overrides an earlier one, so you
can keep hand-written overrides in a separate file next to the generated one.

---

## 4. Write the scenario as a tree

A tree addresses nodes **by alias**, so no node ids or data types ever appear in
the XML — that all lives in the registry. Here is a complete scenario that
connects, reads an identity, takes a lock, writes a value under the lock, then
releases and disconnects:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<BTEng format_version="1.0" main_tree_to_execute="scenario">
  <Tree ID="scenario">
    <Sequence name="root">

      <OpcUaConnect name="Connect"
                    endpoint="opc.tcp://192.168.1.10:4840"
                    session_id="s1"
                    username="operator" password="operator"/>

      <OpcUaRead name="ReadName"
                 session_id="s1" node_alias="component_name"/>

      <OpcUaCallMethod name="Lock"
                       session_id="s1" method_alias="init_lock_method"/>

      <OpcUaWrite name="SetTarget"
                  session_id="s1" node_alias="target" value="42"/>

      <OpcUaCallMethod name="Unlock"
                       session_id="s1" method_alias="exit_lock_method"/>

      <OpcUaDisconnect name="Disconnect" session_id="s1"/>

    </Sequence>
  </Tree>
</BTEng>
```

Common node attributes:

| Node | Key attributes |
|---|---|
| `OpcUaConnect` | `endpoint`, `session_id`, `timeout`, `username`, `password`, `certificate_path`, `private_key_path` |
| `OpcUaDisconnect` | `session_id` |
| `OpcUaRead` | `session_id`, `node_alias`, `value="{key}"` (optional output binding) |
| `OpcUaWrite` | `session_id`, `node_alias`, `value` (literal or `{key}`) |
| `OpcUaCallMethod` | `session_id`, `method_alias`, `output_prefix` |
| `OpcUaCondition` | `session_id`, `node_alias`, `expected_value` |

Two rules worth knowing:

- **Method input arguments** are pulled from the blackboard by argument name
  (the snake-cased server argument name). Seed them with a `SetBlackboard` node
  or the runner's `--bb` preset.
- To route a read into a specific blackboard key, use `value="{my_key}"` **and**
  declare the output port in a `<TreeNodesModel>` block — otherwise the parser
  treats `{my_key}` as an input and the read falls back to storing under its
  alias. See [`reference.md`](reference.md#ports-and-blackboard-keys) for the `<TreeNodesModel>` example.

---

## 5. Run it

No runner to write — the CLI runs one tree, several, or a whole directory:

```bash
bteng-opcua run scenario.xml -n nodes.yaml     # one tree
bteng-opcua run trees/ -n nodes.yaml           # every *.xml in a directory
```

| Flag | Default | Purpose |
|---|---|---|
| `trees` | *(required)* | One or more tree XML files, or a directory of them. |
| `-n`, `--nodes` | `nodes.yaml` | Registry to load. |
| `--bb KEY=VALUE` | — | Seed a blackboard key before each run (repeatable). |
| `--timeout` | `60` | Per-tree timeout in seconds. |
| `--plain` | off | Disable colour/emoji (for log files and CI). |
| `--log` | `INFO` | Log level. |

The process exits `0` only if every tree ends in `SUCCESS`, `1` otherwise — so
it drops straight into CI or a shell `&&` chain.

Each node logs what it does (connect, read `alias = value`, write, method
results) under the `bteng_opcua.*` logger, and by default `run` colours the
lines and prints one emoji per node as it settles (🔌 connect, 📖 read,
✍️ write, ⚙️ method, 🔍 condition, 💤 sleep, ✅/❌ result). The library core
logs plain; the colour/emoji live only in the `run` command, so piped or
CI output stays clean — or force it with `--plain`.

The registry's optional `blackboard:` section seeds values (e.g. method input
arguments) before every tree; `--bb` overrides individual keys.

---

## Offline path: harvest from a NodeSet2.xml

No server handy? Export the model once and harvest from the file:

```bash
python examples/demo_server.py --export models/robot.NodeSet2.xml   # or get a NodeSet2 from the vendor
bteng-opcua harvest -s harvest.yaml -o nodes.yaml                   # with `nodeset:` set in the spec
```

One caveat: namespace indices in a NodeSet2.xml come from that file's own
`<NamespaceUris>` order, which a real server may assign differently. Verify a
file-harvested registry against the live server with `--check` before trusting
its indices.

---

## Try the whole loop against the bundled demo

```bash
python examples/demo_server.py                                            # terminal 1
bteng-opcua harvest -s examples/config/harvest.yaml -o examples/config/nodes.yaml
bteng-opcua run examples/trees/ -n examples/config/nodes.yaml
```

---

## Reference: valid `data_type` values

Scalars:

`int32` · `int64` · `uint16` · `uint32` · `uint64` · `byte` · `float` ·
`double` · `string` · `boolean` · `localized_text` · `method`

Array variants (append `[]`):

`int32[]` · `int64[]` · `uint16[]` · `uint32[]` · `uint64[]` · `byte[]` ·
`float[]` · `double[]` · `string[]` · `boolean[]`

`localized_text` is scalar-only (it reads the `Text` field of an OPC UA
`LocalizedText`; arrays of it are skipped). `DateTime` is read as an ISO string.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `BadIdentityTokenRejected` during harvest | Server has no Anonymous token. Add an `auth:` block or pass `--username/--password`. |
| A node you wanted is missing from `nodes.yaml` | Its DataType has no registry equivalent (see skip list) — check the harvest warnings; `--strict` makes them fail loudly. |
| `bteng-opcua harvest --check` exits `1` in CI | The server's model changed. Re-run without `--check` and commit the new `nodes.yaml`. |
| `BadUserAccessDenied` on a write at run time | The server gates writes behind a lock/method — call it (e.g. an `init_lock_method`) before the write in your tree. |
| Tree can't find an alias | The alias in the XML doesn't match one in the loaded registry — check the generated `nodes.yaml` for the exact snake-cased name. |
