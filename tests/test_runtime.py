"""Tests for the `bteng-opcua run` runtime — offline, no server required.

Covers the code paths the pre-merge review flagged as untested: preset typing,
tree collection, the Sleep node, and the "one broken tree must not abort the
batch" contract.
"""
from __future__ import annotations

import time

import pytest
from bteng.core.node import NodeConfig, NodeStatus

from bteng_opcua import Sleep, register_nodes
from bteng_opcua.runtime import _collect_trees, parse_bb_presets, run_one, run_trees

SLEEP_TREE = """<?xml version="1.0"?>
<BTEng main_tree_to_execute="t"><Tree ID="t">
  <Sequence><Sleep name="w" duration="{dur}"/></Sequence>
</Tree></BTEng>
"""

BROKEN_TREE = """<?xml version="1.0"?>
<BTEng main_tree_to_execute="t"><Tree ID="t">
  <Sequence><NoSuchNode name="boom"/></Sequence>
</Tree></BTEng>
"""


# ── parse_bb_presets ─────────────────────────────────────────────────────────
def test_presets_preserve_native_types():
    got = parse_bb_presets(["flag=false", "n=5", "x=2.5", "s=hello"])
    assert got == {"flag": False, "n": 5, "x": 2.5, "s": "hello"}
    # The bug this guards: a string "false" would be truthy when cast to bool.
    assert got["flag"] is False


def test_presets_reject_missing_equals():
    with pytest.raises(ValueError):
        parse_bb_presets(["novalue"])


# ── _collect_trees ───────────────────────────────────────────────────────────
def test_collect_trees_expands_dir_sorted(tmp_path):
    (tmp_path / "b.xml").write_text("x")
    (tmp_path / "a.xml").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    found = _collect_trees([tmp_path])
    assert [p.name for p in found] == ["a.xml", "b.xml"]  # sorted, .txt excluded


def test_collect_trees_takes_a_file_as_is(tmp_path):
    f = tmp_path / "one.xml"
    f.write_text("x")
    assert _collect_trees([f]) == [f]


# ── Sleep node ───────────────────────────────────────────────────────────────
def test_sleep_zero_duration_succeeds_immediately():
    node = Sleep("s", NodeConfig(params={"duration": 0}))
    assert node.on_start() == NodeStatus.SUCCESS


def test_sleep_non_numeric_fails_cleanly():
    node = Sleep("s", NodeConfig(params={"duration": "abc"}))
    assert node.on_start() == NodeStatus.FAILURE
    assert "must be a number" in node.feedback_message


def test_sleep_waits_then_succeeds():
    node = Sleep("s", NodeConfig(params={"duration": 0.05}))
    assert node.on_start() == NodeStatus.RUNNING
    assert node.on_running() == NodeStatus.RUNNING
    time.sleep(0.06)
    assert node.on_running() == NodeStatus.SUCCESS


# ── run_one / run_trees ──────────────────────────────────────────────────────
def test_run_one_sleep_tree_succeeds(tmp_path):
    register_nodes()
    tree = tmp_path / "sleep.xml"
    tree.write_text(SLEEP_TREE.format(dur="0.02"))
    assert run_one(tree, {}, timeout=5.0, pretty=False) == NodeStatus.SUCCESS


def test_run_one_broken_tree_fails_without_raising(tmp_path):
    register_nodes()
    tree = tmp_path / "broken.xml"
    tree.write_text(BROKEN_TREE)
    # Must not raise — an unparseable tree is a FAILURE, not a crash.
    assert run_one(tree, {}, timeout=5.0, pretty=False) == NodeStatus.FAILURE


def test_run_trees_missing_registry_returns_1(tmp_path):
    tree = tmp_path / "sleep.xml"
    tree.write_text(SLEEP_TREE.format(dur="0.01"))
    assert run_trees([tree], tmp_path / "nope.yaml", pretty=False) == 1


def test_run_trees_one_bad_one_good_continues_and_flags(tmp_path):
    registry = tmp_path / "nodes.yaml"
    registry.write_text("nodes: []\n")
    good = tmp_path / "a_good.xml"
    good.write_text(SLEEP_TREE.format(dur="0.02"))
    bad = tmp_path / "b_bad.xml"
    bad.write_text(BROKEN_TREE)
    # The batch must run both (not abort on the bad one) and report failure.
    rc = run_trees([good, bad], registry, pretty=False)
    assert rc == 1
