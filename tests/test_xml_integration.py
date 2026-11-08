"""Tests for bteng_opcua.xml_integration.

All tests are isolated: no real OPC UA server is required.
Each test resets the BTEng NodeFactory singleton so registrations do not
bleed between tests.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bteng.factory.factory import NodeFactory
from bteng_opcua import BTENG_NODES
from bteng_opcua.xml_integration import load_tree, register_nodes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_factory():
    """Reset the NodeFactory singleton before and after every test."""
    NodeFactory.reset_instance()
    yield
    NodeFactory.reset_instance()


# ---------------------------------------------------------------------------
# register_nodes()
# ---------------------------------------------------------------------------

class TestRegisterNodes:
    def test_runs_without_error(self):
        register_nodes()

    def test_all_opcua_nodes_are_registered(self):
        factory = NodeFactory.get_instance()
        register_nodes(factory)
        for name, _ in BTENG_NODES:
            assert factory.is_registered(name), f"{name!r} not found in factory"

    def test_registered_class_matches(self):
        factory = NodeFactory.get_instance()
        register_nodes(factory)
        for name, cls in BTENG_NODES:
            # The internal registry maps name → class
            assert factory._registry[name] is cls

    def test_idempotent_double_registration(self):
        factory = NodeFactory.get_instance()
        register_nodes(factory)
        register_nodes(factory)  # second call must not raise
        for name, _ in BTENG_NODES:
            assert factory.is_registered(name)

    def test_custom_factory_instance(self):
        """register_nodes() should honour an explicit factory argument."""
        custom = NodeFactory()
        register_nodes(custom)
        for name, _ in BTENG_NODES:
            assert custom.is_registered(name)
        # The global singleton must be unchanged (no nodes registered there)
        global_factory = NodeFactory.get_instance()
        for name, _ in BTENG_NODES:
            assert not global_factory.is_registered(name)

    def test_node_count(self):
        """register_nodes adds the BTENG_NODES plus the bteng blackboard builtins
        (SetBlackboard/CheckBlackboard), which bteng ships but does not
        auto-register."""
        factory = NodeFactory.get_instance()
        before = set(factory.registered_names())
        register_nodes(factory)
        after = set(factory.registered_names())
        newly_added = after - before
        expected = {name for name, _ in BTENG_NODES} | {"SetBlackboard", "CheckBlackboard"}
        assert newly_added == expected

    def test_manifest_created_for_each_node(self):
        factory = NodeFactory.get_instance()
        register_nodes(factory)
        for name, _ in BTENG_NODES:
            manifest = factory.manifest(name)
            assert manifest is not None
            assert manifest.type_name == name

    def test_export_node_models_xml_contains_opcua_nodes(self):
        factory = NodeFactory.get_instance()
        register_nodes(factory)
        xml_str = factory.export_node_models_xml()
        for name, _ in BTENG_NODES:
            assert name in xml_str, f"{name!r} missing from exported XML model"


# ---------------------------------------------------------------------------
# load_tree() — XML parsing
# ---------------------------------------------------------------------------

# Minimal valid BTEng XML that uses a built-in Sequence so we can verify
# the parser works without needing a full OPC UA environment.  The
# OpcUaConnect node is included to prove it is found in the factory; since
# we never tick the tree no real connection is attempted.

_MINIMAL_XML = textwrap.dedent("""\
    <BTEng format_version="1.0" main_tree_to_execute="main">
      <Tree ID="main">
        <Sequence name="root">
          <OpcUaConnect name="connect"
                        url="opc.tcp://localhost:4840"
                        session="{session}" />
        </Sequence>
      </Tree>
    </BTEng>
""")


class TestLoadTree:
    def test_load_tree_returns_root_node(self, tmp_path: Path):
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        root = load_tree(xml_file)
        assert root is not None

    def test_load_tree_registers_opcua_nodes(self, tmp_path: Path):
        """After load_tree, the global factory must have OPC UA nodes registered."""
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        load_tree(xml_file)
        factory = NodeFactory.get_instance()
        for name, _ in BTENG_NODES:
            assert factory.is_registered(name)

    def test_load_tree_root_node_name(self, tmp_path: Path):
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        root = load_tree(xml_file)
        # The outermost node in the XML is the Sequence named "root"
        assert root.name == "root"

    def test_load_tree_with_custom_factory(self, tmp_path: Path):
        """load_tree should accept an explicit factory and not touch the singleton."""
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        custom = NodeFactory()
        root = load_tree(xml_file, factory=custom)
        assert root is not None
        # Custom factory has the OPC UA nodes; global singleton should not
        for name, _ in BTENG_NODES:
            assert custom.is_registered(name)
        global_factory = NodeFactory.get_instance()
        for name, _ in BTENG_NODES:
            assert not global_factory.is_registered(name)

    def test_load_tree_selects_tree_by_id(self, tmp_path: Path):
        xml = textwrap.dedent("""\
            <BTEng format_version="1.0">
              <Tree ID="alpha">
                <Sequence name="alpha_root">
                  <OpcUaConnect name="c1" url="opc.tcp://localhost:4840" session="s" />
                </Sequence>
              </Tree>
              <Tree ID="beta">
                <Sequence name="beta_root">
                  <OpcUaConnect name="c2" url="opc.tcp://localhost:4840" session="s" />
                </Sequence>
              </Tree>
            </BTEng>
        """)
        xml_file = tmp_path / "multi.xml"
        xml_file.write_text(xml)
        root = load_tree(xml_file, tree_id="beta")
        assert root.name == "beta_root"

    def test_load_tree_unknown_tree_id_raises(self, tmp_path: Path):
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        with pytest.raises(KeyError):
            load_tree(xml_file, tree_id="no_such_tree")

    def test_load_tree_opcua_connect_child_accessible(self, tmp_path: Path):
        """The OpcUaConnect leaf must be the Sequence's first child."""
        xml_file = tmp_path / "bt.xml"
        xml_file.write_text(_MINIMAL_XML)
        root = load_tree(xml_file)
        # ControlNode exposes children via get_children() (stored as _children)
        children = root.get_children()
        assert len(children) == 1
        child = children[0]
        assert child.name == "connect"
