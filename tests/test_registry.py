"""Tests for bteng_opcua.registry — node definition loading and resolution."""
import pytest

from bteng_opcua.exceptions import NodeAliasError
from bteng_opcua.registry import (
    ArgDefinition,
    NodeDefinition,
    NodeRegistry,
    get_global_registry,
    reset_global_registry,
    load_global,
)
from bteng_opcua.types import OpcUaDataType


SAMPLE_DICT = {
    "nodes": [
        {
            "alias":      "temperature",
            "node_index": 2,
            "node_id":    "s=Temperature",
            "data_type":  "float",
        },
        {
            "alias":             "move_robot",
            "node_index":        2,
            "node_id":           "",
            "data_type":         "method",
            "object_id":         "ns=2;i=100",
            "method_id":         "ns=2;i=101",
            "condition_node_id": "ns=2;i=50",
            "condition_value":   True,
            "input_args":        ["target_x:double", "target_y:double"],
            "output_args":       [{"name": "result_code", "type": "int32"}],
        },
    ]
}


class TestNodeRegistry:
    def setup_method(self):
        self.reg = NodeRegistry()
        self.reg.load_dict(SAMPLE_DICT)

    def test_resolve_data_node(self):
        defn = self.reg.resolve("temperature")
        assert defn.alias == "temperature"
        assert defn.data_type == OpcUaDataType.FLOAT
        assert defn.node_index == 2
        assert defn.node_id == "s=Temperature"
        assert defn.full_node_id == "ns=2;s=Temperature"
        assert not defn.is_method

    def test_resolve_method_node(self):
        defn = self.reg.resolve("move_robot")
        assert defn.is_method
        assert defn.object_id == "ns=2;i=100"
        assert defn.method_id == "ns=2;i=101"
        assert defn.condition_node_id == "ns=2;i=50"
        assert defn.condition_value is True

    def test_input_args_parsed(self):
        defn = self.reg.resolve("move_robot")
        assert len(defn.input_args) == 2
        assert defn.input_args[0].name == "target_x"
        assert defn.input_args[0].type == OpcUaDataType.DOUBLE
        assert defn.input_args[1].name == "target_y"

    def test_output_args_parsed(self):
        defn = self.reg.resolve("move_robot")
        assert len(defn.output_args) == 1
        assert defn.output_args[0].name == "result_code"
        assert defn.output_args[0].type == OpcUaDataType.INT32

    def test_resolve_unknown_raises(self):
        with pytest.raises(NodeAliasError, match="unknown_alias"):
            self.reg.resolve("unknown_alias")

    def test_has(self):
        assert self.reg.has("temperature")
        assert not self.reg.has("nonexistent")

    def test_all_aliases(self):
        assert set(self.reg.all_aliases()) == {"temperature", "move_robot"}

    def test_load_dict_missing_field_raises(self):
        with pytest.raises(ValueError, match="data_type"):
            NodeRegistry().load_dict({"nodes": [{"alias": "x", "node_index": 1, "node_id": "i=1"}]})

    def test_load_dict_unknown_data_type_raises(self):
        with pytest.raises(ValueError, match="complex"):
            NodeRegistry().load_dict(
                {"nodes": [{"alias": "x", "node_index": 1, "node_id": "i=1", "data_type": "complex"}]}
            )

    def test_method_missing_object_id_raises(self):
        with pytest.raises(ValueError, match="object_id"):
            NodeRegistry().load_dict({
                "nodes": [{
                    "alias": "m", "node_index": 2, "node_id": "",
                    "data_type": "method", "method_id": "ns=2;i=1",
                }]
            })


class TestArgDefinition:
    def test_from_str(self):
        arg = ArgDefinition.from_str("speed:float")
        assert arg.name == "speed"
        assert arg.type == OpcUaDataType.FLOAT

    def test_from_str_invalid_format(self):
        with pytest.raises(ValueError, match="name:type"):
            ArgDefinition.from_str("no_colon")

    def test_from_str_unknown_type(self):
        with pytest.raises(ValueError, match="complex"):
            ArgDefinition.from_str("x:complex")

    def test_from_dict(self):
        arg = ArgDefinition.from_dict({"name": "val", "type": "boolean"})
        assert arg.name == "val"
        assert arg.type == OpcUaDataType.BOOLEAN

    def test_from_str_array_type(self):
        arg = ArgDefinition.from_str("readings:float[]")
        assert arg.name == "readings"
        assert arg.type == OpcUaDataType.FLOAT_ARRAY

    def test_data_node_with_array_type(self):
        reg = NodeRegistry()
        reg.load_dict({"nodes": [{
            "alias": "sensor_readings",
            "node_index": 2,
            "node_id": "s=Readings",
            "data_type": "float[]",
        }]})
        defn = reg.resolve("sensor_readings")
        assert defn.data_type == OpcUaDataType.FLOAT_ARRAY


class TestGlobalRegistry:
    def setup_method(self):
        reset_global_registry()

    def teardown_method(self):
        reset_global_registry()

    def test_get_global_creates_empty(self):
        reg = get_global_registry()
        assert reg.all_aliases() == []

    def test_global_is_singleton(self):
        r1 = get_global_registry()
        r2 = get_global_registry()
        assert r1 is r2

    def test_reset_clears(self):
        get_global_registry().load_dict(SAMPLE_DICT)
        reset_global_registry()
        assert get_global_registry().all_aliases() == []

    def test_load_global_file(self, tmp_path):
        p = tmp_path / "nodes.yaml"
        p.write_text(
            "nodes:\n"
            "  - alias: tmp_node\n"
            "    node_index: 1\n"
            "    node_id: i=999\n"
            "    data_type: int32\n"
        )
        load_global(p)
        assert get_global_registry().has("tmp_node")
