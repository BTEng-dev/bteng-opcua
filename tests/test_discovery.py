"""Tests for the harvest / discovery layer (offline NodeSet2 path)."""
from __future__ import annotations

import textwrap

import pytest

from bteng_opcua.discovery import (
    HarvestAuth,
    HarvestRoot,
    HarvestSpec,
    harvest_from_nodeset,
    snake_case,
)

NODESET = textwrap.dedent("""\
    <?xml version="1.0" encoding="utf-8"?>
    <UANodeSet xmlns:uax="http://opcfoundation.org/UA/2008/02/Types.xsd"
               xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
      <NamespaceUris><Uri>http://example/robot</Uri></NamespaceUris>
      <Aliases>
        <Alias Alias="Boolean">i=1</Alias>
        <Alias Alias="Float">i=10</Alias>
        <Alias Alias="Double">i=11</Alias>
      </Aliases>
      <UAObject NodeId="ns=1;i=1" BrowseName="1:Robot" ParentNodeId="i=85"/>
      <UAVariable NodeId="ns=1;i=2" BrowseName="1:Temperature"
                  ParentNodeId="ns=1;i=1" DataType="Float"/>
      <UAVariable NodeId="ns=1;i=3" BrowseName="1:RobotReady"
                  ParentNodeId="ns=1;i=1" DataType="Boolean"/>
      <UAVariable NodeId="ns=1;i=4" BrowseName="1:Waypoints"
                  ParentNodeId="ns=1;i=1" DataType="Double" ValueRank="1"/>
      <UAVariable NodeId="ns=1;i=9" BrowseName="1:Picture"
                  ParentNodeId="ns=1;i=1" DataType="ByteString"/>
      <UAVariable NodeId="ns=1;i=10" BrowseName="1:ComponentName"
                  ParentNodeId="ns=1;i=1" DataType="LocalizedText"/>
      <UAMethod NodeId="ns=1;i=5" BrowseName="1:MoveToPosition" ParentNodeId="ns=1;i=1"/>
      <UAVariable NodeId="ns=1;i=6" BrowseName="0:InputArguments"
                  ParentNodeId="ns=1;i=5" DataType="Argument">
        <Value>
          <uax:ListOfExtensionObject>
            <uax:ExtensionObject><uax:Body><uax:Argument>
              <uax:Name>target_x</uax:Name>
              <uax:DataType><uax:Identifier>i=11</uax:Identifier></uax:DataType>
              <uax:ValueRank>-1</uax:ValueRank>
            </uax:Argument></uax:Body></uax:ExtensionObject>
          </uax:ListOfExtensionObject>
        </Value>
      </UAVariable>
      <UAVariable NodeId="ns=1;i=7" BrowseName="0:OutputArguments"
                  ParentNodeId="ns=1;i=5" DataType="Argument">
        <Value>
          <uax:ListOfExtensionObject>
            <uax:ExtensionObject><uax:Body><uax:Argument>
              <uax:Name>result_code</uax:Name>
              <uax:DataType><uax:Identifier>i=6</uax:Identifier></uax:DataType>
              <uax:ValueRank>-1</uax:ValueRank>
            </uax:Argument></uax:Body></uax:ExtensionObject>
          </uax:ListOfExtensionObject>
        </Value>
      </UAVariable>
    </UANodeSet>
    """)


@pytest.fixture
def nodeset(tmp_path):
    path = tmp_path / "robot.NodeSet2.xml"
    path.write_text(NODESET, encoding="utf-8")
    return path


def by_alias(defs):
    return {d["alias"]: d for d in defs}


# ── snake_case ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("MoveToPosition", "move_to_position"),
    ("Temperature", "temperature"),
    ("2:RobotReady", "robot_ready"),
    ("Axis-1 Speed", "axis_1_speed"),
])
def test_snake_case(raw, expected):
    assert snake_case(raw) == expected


# ── Spec parsing ──────────────────────────────────────────────────────────────

def test_spec_parses_string_roots_as_path_or_node_id():
    spec = HarvestSpec.from_dict({
        "server": "opc.tcp://x:4840",
        "harvest": ["Objects/Robot", "ns=2;i=1001"],
    })
    assert spec.roots[0].path == "Objects/Robot"
    assert spec.roots[0].node_id is None
    assert spec.roots[1].node_id == "ns=2;i=1001"
    assert spec.roots[1].path is None


def test_spec_requires_harvest_list():
    with pytest.raises(ValueError, match="harvest"):
        HarvestSpec.from_dict({"server": "opc.tcp://x:4840"})


def test_harvest_root_requires_path_or_node_id():
    with pytest.raises(ValueError, match="path"):
        HarvestRoot.parse({"alias": "orphan"})


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_spec_reads_the_auth_section():
    spec = HarvestSpec.from_dict({
        "server": "opc.tcp://x:4840",
        "auth": {"username": "operator", "password": "secret"},
        "harvest": ["Objects/Robot"],
    })
    assert spec.auth.username == "operator"
    assert spec.auth.password == "secret"
    assert spec.auth


def test_spec_without_auth_is_falsy():
    spec = HarvestSpec.from_dict({
        "server": "opc.tcp://x:4840", "harvest": ["Objects/Robot"],
    })
    assert not spec.auth
    assert spec.auth.username is None


def test_auth_is_not_swallowed_into_extra():
    """`auth:` is a known key — it must not leak into the preserved sections."""
    spec = HarvestSpec.from_dict({
        "server": "opc.tcp://x:4840",
        "auth": {"username": "operator"},
        "harvest": ["Objects/Robot"],
    })
    assert "auth" not in spec.extra


def test_cli_overrides_win_over_the_spec_but_none_does_not():
    base = HarvestAuth(username="spec_user", password="spec_pw")
    merged = base.merged_with(
        username="cli_user", password=None,
        certificate_path=None, private_key_path=None,
    )
    assert merged.username == "cli_user"
    assert merged.password == "spec_pw"


def test_certificate_only_auth_is_truthy():
    assert HarvestAuth(certificate_path="/tmp/client.der")


# ── NodeSet2 harvesting ───────────────────────────────────────────────────────

def test_harvest_nodeset_reads_types_from_the_model(nodeset):
    defs, _ = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])
    found = by_alias(defs)

    assert found["temperature"]["data_type"] == "float"
    assert found["temperature"]["node_id"] == "i=2"
    assert found["temperature"]["node_index"] == 1
    assert found["robot_ready"]["data_type"] == "boolean"


def test_value_rank_one_becomes_an_array_type(nodeset):
    defs, _ = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])
    assert by_alias(defs)["waypoints"]["data_type"] == "double[]"


def test_method_signature_is_read_from_arguments(nodeset):
    defs, _ = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])
    method = by_alias(defs)["move_to_position"]

    assert method["data_type"] == "method"
    assert method["object_id"] == "ns=1;i=1"
    assert method["method_id"] == "ns=1;i=5"
    assert method["input_args"] == ["target_x:double"]
    assert method["output_args"] == ["result_code:int32"]


def test_argument_properties_are_not_emitted_as_nodes(nodeset):
    defs, _ = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])
    assert "input_arguments" not in by_alias(defs)
    assert "output_arguments" not in by_alias(defs)


def test_unsupported_data_type_is_skipped_with_a_warning(nodeset):
    defs, warnings = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])

    assert "picture" not in by_alias(defs)  # ByteString has no registry equivalent
    assert any("Picture" in w for w in warnings)


def test_localized_text_is_harvested_not_skipped(nodeset):
    """LocalizedText is pervasive in DI/Machinery models (ComponentName, states)."""
    defs, warnings = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])

    assert by_alias(defs)["component_name"]["data_type"] == "localized_text"
    assert not any("ComponentName" in w for w in warnings)


def test_method_without_argument_values_is_skipped_not_guessed(tmp_path):
    """A NodeSet2 exported without values must not yield a zero-argument method."""
    stripped = NODESET.replace("<Value>", "<NoValue>").replace("</Value>", "</NoValue>")
    path = tmp_path / "novalues.NodeSet2.xml"
    path.write_text(stripped, encoding="utf-8")

    defs, warnings = harvest_from_nodeset(path, [HarvestRoot(path="Objects/Robot")])

    assert "move_to_position" not in by_alias(defs)
    assert any("unreadable" in w for w in warnings)


def test_single_node_root_with_explicit_alias(nodeset):
    defs, _ = harvest_from_nodeset(
        nodeset, [HarvestRoot(node_id="ns=1;i=2", alias="temp_c")]
    )
    assert [d["alias"] for d in defs] == ["temp_c"]
    assert defs[0]["data_type"] == "float"


def test_unknown_browse_path_warns_and_skips(nodeset):
    defs, warnings = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Nope")])
    assert defs == []
    assert any("Nope" in w for w in warnings)


def test_definitions_load_into_the_registry(nodeset):
    """The generated dicts must satisfy NodeRegistry's schema and validation."""
    from bteng_opcua.registry import NodeRegistry

    defs, _ = harvest_from_nodeset(nodeset, [HarvestRoot(path="Objects/Robot")])
    registry = NodeRegistry()
    registry.load_dict({"nodes": defs})

    assert registry.resolve("temperature").full_node_id == "ns=1;i=2"
    assert registry.resolve("move_to_position").is_method
    assert [a.name for a in registry.resolve("move_to_position").input_args] == ["target_x"]
