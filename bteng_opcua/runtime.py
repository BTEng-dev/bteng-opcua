"""Run behaviour trees with readable output — the application layer.

The library core logs plain ``logging`` records (see the node modules); this
module is where a *run* becomes a run: a synchronous tick loop, session
cleanup, failure reporting, and — for a terminal — colour and one emoji line
per node as it settles.  ``bteng-opcua run`` is a thin CLI over ``run_trees``.

Colour/emoji live here, not in the library core, so file and CI logs stay
clean; ``pretty=False`` (``--plain``) turns them off.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from pathlib import Path

import yaml
from bteng import Blackboard, NodeStatus

from bteng_opcua import OpcUaSessionManager, load_nodes, load_tree, register_nodes

logger = logging.getLogger("bteng_opcua.run")

# ── Colour formatting ───────────────────────────────────────────────────────────
_RESET  = "\033[0m"
_COLORS = {
    "DEBUG":    "\033[90m",
    "INFO":     "\033[36m",
    "WARNING":  "\033[33m",
    "ERROR":    "\033[31m",
    "CRITICAL": "\033[1;41m",
}


def _color_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR") is not None:
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class _ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool) -> None:
        super().__init__(fmt="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
                         datefmt="%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self.use_color:
            return text
        color = _COLORS.get(record.levelname, "")
        return f"{color}{text}{_RESET}" if color else text


def install_logging(level: str = "INFO", pretty: bool = True) -> None:
    """Configure the root logger for a run.  Colours only when ``pretty`` and a
    TTY; asyncua is quieted so our lines stand out."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ColorFormatter(pretty and _color_enabled(sys.stdout)))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("asyncua").setLevel(logging.WARNING)


# ── Per-node emoji reporting ────────────────────────────────────────────────────
_NODE_EMOJI = [
    ("OpcUaConnect",      "🔌"),
    ("OpcUaDisconnect",   "🔻"),
    ("OpcUaReconnect",    "🔄"),
    ("OpcUaBatchRead",    "📚"),
    ("OpcUaBatchWrite",   "🖊️"),
    ("OpcUaRead",         "📖"),
    ("OpcUaWrite",        "✍️"),
    ("OpcUaCallMethod",   "⚙️"),
    ("OpcUaCondition",    "🔍"),
    ("OpcUaSubscribe",    "📡"),
    ("OpcUaWaitForValue", "⏳"),
    ("Sleep",             "💤"),
    ("SetBlackboard",     "📝"),
    ("CheckBlackboard",   "🔎"),
]
_STATUS_MARK = {
    NodeStatus.SUCCESS: ("✅", logging.INFO),
    NodeStatus.FAILURE: ("❌", logging.ERROR),
}


def _emoji_for(node) -> str:
    name = type(node).__name__
    for needle, emoji in _NODE_EMOJI:
        if needle in name:
            return emoji
    return "•"


def _leaves(node, acc=None):
    acc = [] if acc is None else acc
    children = node.get_children()
    if children:
        for child in children:
            _leaves(child, acc)
    else:
        acc.append(node)
    return acc


class _NodeReporter:
    """Log one emoji line each time a leaf settles (SUCCESS/FAILURE)."""

    def __init__(self, root, enabled: bool) -> None:
        self.enabled = enabled
        self._leaves = _leaves(root) if enabled else []
        self._last: dict[int, NodeStatus] = {}

    def poll(self) -> None:
        if not self.enabled:
            return
        for node in self._leaves:
            status = node.status
            if status == self._last.get(id(node)):
                continue
            self._last[id(node)] = status
            mark = _STATUS_MARK.get(status)
            if mark is None:
                continue
            symbol, level = mark
            detail = ""
            if status == NodeStatus.FAILURE and node.feedback_message:
                detail = f" — {node.feedback_message}"
            logger.log(level, "%s %s %s%s", _emoji_for(node), symbol, node.name, detail)


class TreeRunner:
    """Tick a tree synchronously until it settles or times out."""

    def __init__(self, root, tick_interval: float = 0.01, timeout: float = 60.0,
                 pretty: bool = True) -> None:
        self.root = root
        self.tick_interval = tick_interval
        self.timeout = timeout
        self.pretty = pretty

    def run(self) -> NodeStatus:
        if self.pretty:
            logger.info("🌳 Tree execution started (timeout=%.1fs)", self.timeout)
        reporter = _NodeReporter(self.root, self.pretty)
        start = time.monotonic()
        ticks = 0
        while True:
            status = self.root.execute_tick()
            ticks += 1
            reporter.poll()
            if status != NodeStatus.RUNNING:
                if self.pretty:
                    emoji = "🎉" if status == NodeStatus.SUCCESS else "🛑"
                    elapsed = time.monotonic() - start
                    logger.info("%s Tree finished: %s  (ticks=%d, elapsed=%.2fs)",
                                emoji, status.value, ticks, elapsed)
                return status
            if time.monotonic() - start >= self.timeout:
                self.root.halt()
                logger.error("⏰ Tree timed out after %.1fs (%d ticks)", self.timeout, ticks)
                return NodeStatus.FAILURE
            time.sleep(self.tick_interval)


# ── Session lifecycle + failure reporting ────────────────────────────────────────
def shutdown() -> None:
    """Close any session a tree left open, then stop the manager's loop.

    A tree that fails before its OpcUaDisconnect leaves a live client behind;
    tearing the loop down under it makes asyncua spew shutdown tracebacks.
    """
    manager = OpcUaSessionManager.get_instance()
    for session_id in list(manager.active_sessions()):
        with contextlib.suppress(Exception):
            manager.disconnect(session_id)
    OpcUaSessionManager.reset_instance()


def failures(node, found: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
    """Collect feedback from nodes that ended in FAILURE.

    Filtering on status keeps a successful node's incidental feedback (e.g.
    'Session connected') out of the failure report.
    """
    found = [] if found is None else found
    if getattr(node, "status", None) == NodeStatus.FAILURE:
        message = getattr(node, "feedback_message", None) or getattr(node, "failure_reason", None)
        if message:
            found.append((getattr(node, "name", node.__class__.__name__), message))
    for child in node.get_children():
        failures(child, found)
    return found


# ── Orchestration ────────────────────────────────────────────────────────────────
def parse_bb_presets(raw: list[str]) -> dict[str, object]:
    """Parse repeated KEY=VALUE strings into a dict.

    Values are decoded with a YAML scalar pass so they carry native types, the
    same as the registry's ``blackboard:`` section: ``x=false`` → ``False``,
    ``n=5`` → ``5``, ``s=abc`` → ``"abc"``.  Without this, every preset would be
    a string and ``--bb enabled=false`` would write ``bool("false")`` == True.
    """
    result: dict[str, object] = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"blackboard preset must be KEY=VALUE, got: {item!r}")
        key, _, value = item.partition("=")
        try:
            parsed = yaml.safe_load(value.strip())
        except yaml.YAMLError:
            parsed = value.strip()
        result[key.strip()] = parsed
    return result


def _collect_trees(paths: list[str | Path]) -> list[Path]:
    """Expand each path: a directory contributes its *.xml (sorted); a file is
    taken as-is."""
    trees: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            trees.extend(sorted(p.glob("*.xml")))
        else:
            trees.append(p)
    return trees


def run_one(path: Path, presets: dict[str, object], timeout: float, pretty: bool) -> NodeStatus:
    """Run a single tree to completion and return its final status.

    Any failure to build or tick the tree (unparseable XML, unknown node tag, a
    bad input like a non-numeric Sleep duration) is turned into a FAILURE result
    rather than an exception, so one broken tree never aborts a batch.
    """
    Blackboard.reset("run")
    bb = Blackboard.create("run")
    for key, value in presets.items():
        bb.set(key, value)

    root = None
    try:
        root = load_tree(path, blackboard=bb)
        status = TreeRunner(root, timeout=timeout, pretty=pretty).run()
    except Exception as exc:
        logger.error("%s failed to run: %s", path.name, exc)
        status = NodeStatus.FAILURE
    finally:
        shutdown()

    if status != NodeStatus.SUCCESS and root is not None:
        for name, message in failures(root):
            logger.error("  %s: %s", name, message)
    return status


def run_trees(paths, nodes_path, presets=None, timeout: float = 60.0,
              pretty: bool = True) -> int:
    """Register nodes, load the registry, run every tree, print a summary.

    Returns a process exit code: 0 if every tree ended in SUCCESS, else 1.

    Note: this does NOT configure logging — call ``install_logging`` first (the
    CLI does) or let your own logging config stand.  A library caller keeps its
    handlers; only the ``bteng-opcua run`` entry point installs the pretty one.
    """
    nodes = Path(nodes_path)
    if not nodes.exists():
        logger.error("Node registry not found: %s", nodes)
        return 1

    trees = _collect_trees(paths if isinstance(paths, list) else [paths])
    if not trees:
        logger.error("No trees to run")
        return 1

    register_nodes()
    load_nodes(nodes)

    # The registry's optional `blackboard:` section seeds values (e.g. method
    # input arguments) before every tree; --bb presets override it per key.
    config = yaml.safe_load(nodes.read_text(encoding="utf-8")) or {}
    seed = {str(k): v for k, v in (config.get("blackboard") or {}).items()}
    seed.update(parse_bb_presets(presets or []))
    presets = seed

    results: list[tuple[str, NodeStatus]] = []
    try:
        for path in trees:
            if not path.exists():
                logger.error("Tree not found: %s", path)
                results.append((path.name, NodeStatus.FAILURE))
                continue
            logger.info("── %s ──", path.name)
            results.append((path.name, run_one(path, presets, timeout, pretty)))
    finally:
        OpcUaSessionManager.reset_instance()

    print("\n=== Summary ===")
    for name, status in results:
        mark = "✅" if status == NodeStatus.SUCCESS else "❌"
        print(f"  {mark if pretty else status.value[:4]} {name:<22} {status.value}")

    return 0 if all(s == NodeStatus.SUCCESS for _, s in results) else 1
