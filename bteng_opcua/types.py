"""OPC UA ↔ Python type system."""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from bteng_opcua.exceptions import TypeConversionError


class OpcUaDataType(str, Enum):
    INT32   = "int32"
    INT64   = "int64"
    FLOAT   = "float"
    DOUBLE  = "double"
    STRING  = "string"
    BOOLEAN = "boolean"
    UINT16  = "uint16"
    UINT32  = "uint32"
    UINT64  = "uint64"
    BYTE    = "byte"
    METHOD  = "method"
    # LocalizedText is carried as its Text field; the Locale is not preserved.
    LOCALIZED_TEXT = "localized_text"
    # Array variants — element type is the scalar name before "[]"
    INT32_ARRAY   = "int32[]"
    INT64_ARRAY   = "int64[]"
    FLOAT_ARRAY   = "float[]"
    DOUBLE_ARRAY  = "double[]"
    STRING_ARRAY  = "string[]"
    BOOLEAN_ARRAY = "boolean[]"
    UINT16_ARRAY  = "uint16[]"
    UINT32_ARRAY  = "uint32[]"
    UINT64_ARRAY  = "uint64[]"
    BYTE_ARRAY    = "byte[]"


def _localized_text_to_str(value: Any) -> str:
    """asyncua LocalizedText → its Text field; anything else → str()."""
    text = getattr(value, "Text", None)
    return str(value) if text is None else str(text)


_PYTHON_CONVERTERS: dict[OpcUaDataType, Callable[[Any], Any]] = {
    OpcUaDataType.LOCALIZED_TEXT: _localized_text_to_str,
    OpcUaDataType.INT32:   int,
    OpcUaDataType.INT64:   int,
    OpcUaDataType.FLOAT:   float,
    OpcUaDataType.DOUBLE:  float,
    OpcUaDataType.STRING:  str,
    OpcUaDataType.BOOLEAN: bool,
    OpcUaDataType.UINT16:  int,
    OpcUaDataType.UINT32:  int,
    OpcUaDataType.UINT64:  int,
    OpcUaDataType.BYTE:    int,
    # Array types — real element conversion handled in to_python(); list is a placeholder.
    OpcUaDataType.INT32_ARRAY:   list,
    OpcUaDataType.INT64_ARRAY:   list,
    OpcUaDataType.FLOAT_ARRAY:   list,
    OpcUaDataType.DOUBLE_ARRAY:  list,
    OpcUaDataType.STRING_ARRAY:  list,
    OpcUaDataType.BOOLEAN_ARRAY: list,
    OpcUaDataType.UINT16_ARRAY:  list,
    OpcUaDataType.UINT32_ARRAY:  list,
    OpcUaDataType.UINT64_ARRAY:  list,
    OpcUaDataType.BYTE_ARRAY:    list,
}


def to_python(value: Any, data_type: OpcUaDataType) -> Any:
    """Convert an OPC UA value to its canonical Python type.

    For array types (e.g. FLOAT_ARRAY), each element is converted using the
    corresponding scalar converter.  A non-iterable scalar input is wrapped in
    a single-element list before conversion.
    """
    # Array types: convert each element using the scalar variant
    if data_type.value.endswith("[]"):
        scalar_type_str = data_type.value[:-2]  # strip "[]"
        try:
            scalar_type = OpcUaDataType(scalar_type_str)
        except ValueError as exc:
            raise TypeConversionError(
                f"Unknown scalar type in array: '{scalar_type_str}'"
            ) from exc
        if not hasattr(value, "__iter__") or isinstance(value, str):
            value = [value]  # wrap scalar / bare string in list
        scalar_converter = _PYTHON_CONVERTERS.get(scalar_type)
        if scalar_converter is None:
            raise TypeConversionError(
                f"No converter for scalar type '{scalar_type}'"
            )
        try:
            return [scalar_converter(v) for v in value]
        except (ValueError, TypeError) as exc:
            raise TypeConversionError(
                f"Cannot convert array to {data_type.value}: {exc}"
            ) from exc

    # Scalar types
    converter = _PYTHON_CONVERTERS.get(data_type)
    if converter is None:
        raise TypeConversionError(f"No Python converter for OPC UA type '{data_type}'")
    try:
        return converter(value)
    except (ValueError, TypeError) as exc:
        raise TypeConversionError(
            f"Cannot convert {value!r} to {data_type.value}: {exc}"
        ) from exc


def get_variant_type(data_type: OpcUaDataType) -> Any:
    """Return the asyncua VariantType for the given OpcUaDataType.

    Returns None when asyncua is not installed so that mock backends
    (which ignore variant_type) still work without the library present.

    For array types asyncua uses the same VariantType as the scalar variant;
    the array flag is conveyed by passing a list to ua.Variant.
    """
    try:
        from asyncua import ua
    except ImportError:
        return None  # asyncua absent; caller must handle None (mock backends do)

    # If this is an array type, resolve the scalar VariantType recursively.
    if data_type.value.endswith("[]"):
        scalar_type_str = data_type.value[:-2]
        try:
            scalar_type = OpcUaDataType(scalar_type_str)
        except ValueError as exc:
            raise TypeConversionError(
                f"Unknown scalar type in array: '{scalar_type_str}'"
            ) from exc
        return get_variant_type(scalar_type)

    mapping = {
        OpcUaDataType.INT32:   ua.VariantType.Int32,
        OpcUaDataType.INT64:   ua.VariantType.Int64,
        OpcUaDataType.FLOAT:   ua.VariantType.Float,
        OpcUaDataType.DOUBLE:  ua.VariantType.Double,
        OpcUaDataType.STRING:  ua.VariantType.String,
        OpcUaDataType.BOOLEAN: ua.VariantType.Boolean,
        OpcUaDataType.UINT16:  ua.VariantType.UInt16,
        OpcUaDataType.UINT32:  ua.VariantType.UInt32,
        OpcUaDataType.UINT64:  ua.VariantType.UInt64,
        OpcUaDataType.BYTE:    ua.VariantType.Byte,
        OpcUaDataType.LOCALIZED_TEXT: ua.VariantType.LocalizedText,
    }
    vt = mapping.get(data_type)
    if vt is None:
        raise TypeConversionError(f"No asyncua VariantType for '{data_type}'")
    return vt


def to_opcua_variant(value: Any, data_type: OpcUaDataType) -> Any:
    """Wrap a Python value in an asyncua Variant with explicit type.

    Falls back to the raw Python value when asyncua is not installed so
    that mock backends (which accept any object as a variant) still work.

    For array types the value is converted to a Python list and wrapped in a
    Variant with the scalar VariantType; asyncua infers the array flag from
    the list type automatically.
    """
    python_value = to_python(value, data_type)
    variant_type = get_variant_type(data_type)
    if variant_type is None:
        return python_value  # asyncua absent; raw value passes through to mock

    from asyncua import ua  # already imported by get_variant_type, no-op cost
    if data_type is OpcUaDataType.LOCALIZED_TEXT:
        # The Variant payload must be a LocalizedText, not the bare Text string.
        python_value = ua.LocalizedText(python_value)
    return ua.Variant(python_value, variant_type)
