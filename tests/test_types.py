"""Tests for bteng_opcua.types — Python ↔ OPC UA type conversion."""
import pytest

from bteng_opcua.exceptions import TypeConversionError
from bteng_opcua.types import OpcUaDataType, to_python


class TestToPython:
    def test_int32(self):
        assert to_python(7, OpcUaDataType.INT32) == 7
        assert isinstance(to_python(7, OpcUaDataType.INT32), int)

    def test_float_from_int(self):
        assert to_python(3, OpcUaDataType.FLOAT) == pytest.approx(3.0)

    def test_double_from_str(self):
        assert to_python("3.14", OpcUaDataType.DOUBLE) == pytest.approx(3.14)

    def test_boolean_from_int(self):
        assert to_python(1, OpcUaDataType.BOOLEAN) is True
        assert to_python(0, OpcUaDataType.BOOLEAN) is False

    def test_string(self):
        assert to_python(42, OpcUaDataType.STRING) == "42"

    def test_uint32(self):
        assert to_python(4_000_000_000, OpcUaDataType.UINT32) == 4_000_000_000

    def test_method_type_raises(self):
        with pytest.raises(TypeConversionError):
            to_python(None, OpcUaDataType.METHOD)

    def test_invalid_value_raises(self):
        with pytest.raises(TypeConversionError):
            to_python("not-a-number", OpcUaDataType.INT32)


class TestOpcUaDataTypeEnum:
    def test_all_values_exist(self):
        expected = {
            "int32", "int64", "float", "double", "string",
            "boolean", "uint16", "uint32", "uint64", "byte", "method",
            "localized_text",
            # array variants
            "int32[]", "int64[]", "float[]", "double[]", "string[]",
            "boolean[]", "uint16[]", "uint32[]", "uint64[]", "byte[]",
        }
        assert {t.value for t in OpcUaDataType} == expected

    def test_from_string(self):
        assert OpcUaDataType("float") == OpcUaDataType.FLOAT

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            OpcUaDataType("complex128")


class TestLocalizedText:
    class _LT:
        """Stand-in for asyncua's ua.LocalizedText."""
        def __init__(self, text, locale="en-US"):
            self.Text, self.Locale = text, locale

    def test_localized_text_yields_its_text_field(self):
        value = self._LT("DummyModule")
        assert to_python(value, OpcUaDataType.LOCALIZED_TEXT) == "DummyModule"

    def test_plain_string_passes_through(self):
        assert to_python("Freed", OpcUaDataType.LOCALIZED_TEXT) == "Freed"

    def test_empty_text_is_not_treated_as_missing(self):
        assert to_python(self._LT(""), OpcUaDataType.LOCALIZED_TEXT) == ""


class TestArrayTypes:
    def test_float_array_from_list(self):
        result = to_python([1, 2, 3], OpcUaDataType.FLOAT_ARRAY)
        assert result == [1.0, 2.0, 3.0]
        assert all(isinstance(v, float) for v in result)

    def test_int32_array(self):
        result = to_python([1.5, 2.7], OpcUaDataType.INT32_ARRAY)
        assert result == [1, 2]
        assert all(isinstance(v, int) for v in result)

    def test_boolean_array(self):
        result = to_python([1, 0, 1], OpcUaDataType.BOOLEAN_ARRAY)
        assert result == [True, False, True]

    def test_scalar_wrapped_in_list(self):
        # A single non-iterable scalar is wrapped into a one-element list
        result = to_python(42, OpcUaDataType.INT32_ARRAY)
        assert result == [42]

    def test_array_type_enum_values(self):
        # All array types must be parseable by value
        array_values = [
            "int32[]", "int64[]", "float[]", "double[]", "string[]",
            "boolean[]", "uint16[]", "uint32[]", "uint64[]", "byte[]",
        ]
        for v in array_values:
            dt = OpcUaDataType(v)
            assert dt.value == v

    def test_string_array(self):
        result = to_python(["a", "b"], OpcUaDataType.STRING_ARRAY)
        assert result == ["a", "b"]
        assert all(isinstance(v, str) for v in result)

    def test_double_array(self):
        result = to_python(["1.1", "2.2"], OpcUaDataType.DOUBLE_ARRAY)
        assert result == pytest.approx([1.1, 2.2])

    def test_invalid_array_element_raises(self):
        with pytest.raises(TypeConversionError):
            to_python(["not-a-number", "bad"], OpcUaDataType.INT32_ARRAY)
