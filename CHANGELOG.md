# Changelog


## [0.2.3] - 2026-11-08

### Fixed
- README now gives the PyPI install command and uses absolute links, so its links work on the PyPI project page. 0.2.2 on PyPI told readers the package was not on PyPI yet. No code changes.

## [0.2.2] - 2026-11-08 (first PyPI upload)

### Changed
- Require `bteng >= 0.3.2` (was `>= 0.3.0`) and Python >= 3.12. Nothing here needs
  the newer engine — 197 tests pass identically on 0.3.0 and 0.3.2, and the single
  example tree parses the same — but 0.3.2 is the first release requiring Python
  3.12, and bteng 0.3.0 and 0.3.1 are yanked on PyPI with no reason recorded, so
  the old floor resolved to a yanked artifact. The behaviour floor is still 0.3.0.
  No OPC UA tree uses a control or decorator node, so 0.3.2's change to bind
  `{key}` references on their attributes does not reach this package.
- CI installs `bteng>=0.3.2` from PyPI instead of `git+…/bteng.git`. The git form
  pinned nothing and, once bteng main required Python 3.12, could not install on
  the 3.10 and 3.11 legs at all. The matrix is now `["3.12"]`.
- `ruff` target-version and `mypy` python_version follow to 3.12.

### Fixed
- `read_history.py` uses `datetime.UTC` instead of `datetime.timezone.utc`
  (ruff UP017, surfaced by the target-version bump). Pure alias, no behaviour
  change.

## [0.2.2] - 2026-07-28 (tagged, not published)

### Changed
- Require `bteng >= 0.3.0` (was `>= 0.1.0`), in both `pyproject.toml` and the README.

## [0.2.0] - 2026-07-27

### Added
- `bteng-opcua harvest` — generate the node registry from a live server's information model or a NodeSet2.xml, so addresses, data types and method signatures need not be written by hand. `--check` fails on drift (CI gate); `--strict` fails on any skipped node.
- Harvest authentication: spec `auth:` section (username/password or certificate/private-key) and `--username/--password/--certificate/--private-key` overrides, for servers that reject anonymous sessions.
- `localized_text` data type — OPC UA `LocalizedText` reads as its `.Text`, writes back as `ua.LocalizedText` (scalar only).
- `bteng-opcua run` — run one tree, several, or a whole directory against a registry, with a tick loop, session cleanup, per-tree summary, exit code, and colour/emoji output (`--plain` to disable, `--bb KEY=VALUE` to seed the blackboard).
- `Sleep` node (generic timed wait), and auto-registration of bteng's `SetBlackboard`/`CheckBlackboard` from `register_nodes()`.
- Plain per-node logging (connect/read/write/method/condition) under the `bteng_opcua.*` logger; a NullHandler keeps the library quiet unless the host configures logging.
- `docs/authoring-nodes.md` walkthrough; `examples/` restructured as a config+trees mini project.

### Changed
- Nodes report values through `logging`; colour/emoji live only in the `run` command, not the library core.

## [0.1.0] - 2026-05-17

### Added
- 12 OPC UA BT nodes: Connect, Disconnect, Read, Write, CallMethod, Subscribe, BatchRead, BatchWrite, ReadHistory, Condition, Reconnect, WaitForValue
- Node registry with YAML-based alias definitions
- AsyncUaBackend via asyncua 1.0+
- XML/BTEng factory integration
- GitHub Actions CI (Python 3.10–3.12)
