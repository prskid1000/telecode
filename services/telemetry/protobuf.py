"""A minimal, schema-driven protobuf decoder for the three OTLP export requests.

No dependency: OTLP/HTTP with ``application/x-protobuf`` is decoded here into
the same dict shape as OTLP/JSON (camelCase keys, trace/span ids as lowercase
hex), so :mod:`services.telemetry.otlp` has one normaliser for both encodings.
Only the fields telecode stores are described; every other field is skipped by
wire type, so newer OTLP versions still decode.

Field numbers: opentelemetry-proto v1 (``collector/*/v1``, ``common/v1``,
``resource/v1``, ``metrics/v1``, ``logs/v1``, ``trace/v1``).
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Tuple

# (json_name, type, repeated[, message]) — type: str | bytes | hex | varint | svarint |
# fixed64 | sfixed64 | fixed32 | double | bool | msg
F = Tuple

SCHEMA: Dict[str, Dict[int, F]] = {
    "AnyValue": {1: ("stringValue", "str", False), 2: ("boolValue", "bool", False),
                 3: ("intValue", "varint", False), 4: ("doubleValue", "double", False),
                 5: ("arrayValue", "msg", False, "ArrayValue"), 6: ("kvlistValue", "msg", False, "KeyValueList"),
                 7: ("bytesValue", "hex", False)},
    "ArrayValue": {1: ("values", "msg", True, "AnyValue")},
    "KeyValueList": {1: ("values", "msg", True, "KeyValue")},
    "KeyValue": {1: ("key", "str", False), 2: ("value", "msg", False, "AnyValue")},
    "Resource": {1: ("attributes", "msg", True, "KeyValue")},
    "Scope": {1: ("name", "str", False), 2: ("version", "str", False), 3: ("attributes", "msg", True, "KeyValue")},
    # metrics
    "MetricsRequest": {1: ("resourceMetrics", "msg", True, "ResourceMetrics")},
    "ResourceMetrics": {1: ("resource", "msg", False, "Resource"), 2: ("scopeMetrics", "msg", True, "ScopeMetrics")},
    "ScopeMetrics": {1: ("scope", "msg", False, "Scope"), 2: ("metrics", "msg", True, "Metric")},
    "Metric": {1: ("name", "str", False), 2: ("description", "str", False), 3: ("unit", "str", False),
               5: ("gauge", "msg", False, "Gauge"), 7: ("sum", "msg", False, "Sum"),
               9: ("histogram", "msg", False, "Histogram")},
    "Gauge": {1: ("dataPoints", "msg", True, "NumberDataPoint")},
    "Sum": {1: ("dataPoints", "msg", True, "NumberDataPoint"), 2: ("aggregationTemporality", "varint", False),
            3: ("isMonotonic", "bool", False)},
    "Histogram": {1: ("dataPoints", "msg", True, "HistogramDataPoint"),
                  2: ("aggregationTemporality", "varint", False)},
    "NumberDataPoint": {7: ("attributes", "msg", True, "KeyValue"), 2: ("startTimeUnixNano", "fixed64", False),
                        3: ("timeUnixNano", "fixed64", False), 4: ("asDouble", "double", False),
                        6: ("asInt", "sfixed64", False)},
    "HistogramDataPoint": {9: ("attributes", "msg", True, "KeyValue"), 2: ("startTimeUnixNano", "fixed64", False),
                           3: ("timeUnixNano", "fixed64", False), 4: ("count", "fixed64", False),
                           5: ("sum", "double", False), 11: ("min", "double", False), 12: ("max", "double", False)},
    # logs
    "LogsRequest": {1: ("resourceLogs", "msg", True, "ResourceLogs")},
    "ResourceLogs": {1: ("resource", "msg", False, "Resource"), 2: ("scopeLogs", "msg", True, "ScopeLogs")},
    "ScopeLogs": {1: ("scope", "msg", False, "Scope"), 2: ("logRecords", "msg", True, "LogRecord")},
    "LogRecord": {1: ("timeUnixNano", "fixed64", False), 11: ("observedTimeUnixNano", "fixed64", False),
                  2: ("severityNumber", "varint", False), 3: ("severityText", "str", False),
                  5: ("body", "msg", False, "AnyValue"), 6: ("attributes", "msg", True, "KeyValue"),
                  9: ("traceId", "hex", False), 10: ("spanId", "hex", False), 12: ("eventName", "str", False)},
    # traces
    "TracesRequest": {1: ("resourceSpans", "msg", True, "ResourceSpans")},
    "ResourceSpans": {1: ("resource", "msg", False, "Resource"), 2: ("scopeSpans", "msg", True, "ScopeSpans")},
    "ScopeSpans": {1: ("scope", "msg", False, "Scope"), 2: ("spans", "msg", True, "Span")},
    "Span": {1: ("traceId", "hex", False), 2: ("spanId", "hex", False), 4: ("parentSpanId", "hex", False),
             5: ("name", "str", False), 6: ("kind", "varint", False), 7: ("startTimeUnixNano", "fixed64", False),
             8: ("endTimeUnixNano", "fixed64", False), 9: ("attributes", "msg", True, "KeyValue"),
             15: ("status", "msg", False, "Status")},
    "Status": {2: ("message", "str", False), 3: ("code", "varint", False)},
}

ROOTS = {"metrics": "MetricsRequest", "logs": "LogsRequest", "traces": "TracesRequest"}


class DecodeError(ValueError):
    pass


def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    shift = result = 0
    while True:
        if i >= len(buf):
            raise DecodeError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise DecodeError("varint too long")


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def decode(buf: bytes, message: str, _depth: int = 0) -> Dict[str, Any]:
    if _depth > 32:
        raise DecodeError("message nesting too deep")
    schema = SCHEMA[message]
    out: Dict[str, Any] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        fno, wt = key >> 3, key & 7
        if wt == 0:
            raw, i = _varint(buf, i)
            val: Any = raw
        elif wt == 1:
            if i + 8 > n:
                raise DecodeError("truncated fixed64")
            val = buf[i:i + 8]
            i += 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            if i + ln > n:
                raise DecodeError("truncated length-delimited field")
            val = buf[i:i + ln]
            i += ln
        elif wt == 5:
            if i + 4 > n:
                raise DecodeError("truncated fixed32")
            val = buf[i:i + 4]
            i += 4
        else:
            raise DecodeError(f"unsupported wire type {wt}")
        spec = schema.get(fno)
        if spec is None:
            continue
        name, typ, repeated = spec[0], spec[1], spec[2]
        try:
            if typ == "msg":
                v: Any = decode(val, spec[3], _depth + 1)
            elif typ == "str":
                v = val.decode("utf-8", "replace")
            elif typ == "hex":
                v = val.hex()
            elif typ == "bytes":
                v = val
            elif typ == "varint":
                v = _signed64(val) if isinstance(val, int) else 0
            elif typ == "bool":
                v = bool(val)
            elif typ == "fixed64":
                v = struct.unpack("<Q", val)[0]
            elif typ == "sfixed64":
                v = struct.unpack("<q", val)[0]
            elif typ == "double":
                v = struct.unpack("<d", val)[0]
            elif typ == "fixed32":
                v = struct.unpack("<I", val)[0]
            else:
                continue
        except (struct.error, TypeError, AttributeError) as exc:
            raise DecodeError(f"bad field {message}.{name}: {exc}") from None
        if repeated:
            out.setdefault(name, []).append(v)
        else:
            out[name] = v
    return out


def decode_request(signal: str, body: bytes) -> Dict[str, Any]:
    """Decode an ``Export{Metrics,Logs,Trace}ServiceRequest`` into OTLP/JSON shape."""
    return decode(body, ROOTS[signal])


# ── encoder (tests + recorded fixtures only) ───────────────────────────────

def _enc_varint(v: int) -> bytes:
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode(obj: Dict[str, Any], message: str) -> bytes:
    """The inverse of :func:`decode` for the described fields (used to build
    protobuf fixtures from recorded JSON payloads)."""
    schema = SCHEMA[message]
    by_name = {spec[0]: (fno, spec) for fno, spec in schema.items()}
    out = bytearray()
    for name, value in obj.items():
        if name not in by_name:
            continue
        fno, spec = by_name[name]
        typ, repeated = spec[1], spec[2]
        for v in (value if repeated else [value]):
            if typ == "msg":
                payload = encode(v, spec[3])
                out += _enc_varint((fno << 3) | 2) + _enc_varint(len(payload)) + payload
            elif typ in ("str", "hex", "bytes"):
                b = v.encode("utf-8") if typ == "str" else bytes.fromhex(v) if typ == "hex" else v
                out += _enc_varint((fno << 3) | 2) + _enc_varint(len(b)) + b
            elif typ in ("varint", "bool"):
                out += _enc_varint(fno << 3) + _enc_varint(int(v))
            elif typ == "fixed64":
                out += _enc_varint((fno << 3) | 1) + struct.pack("<Q", int(v))
            elif typ == "sfixed64":
                out += _enc_varint((fno << 3) | 1) + struct.pack("<q", int(v))
            elif typ == "double":
                out += _enc_varint((fno << 3) | 1) + struct.pack("<d", float(v))
            elif typ == "fixed32":
                out += _enc_varint((fno << 3) | 5) + struct.pack("<I", int(v))
    return bytes(out)


def encode_request(signal: str, obj: Dict[str, Any]) -> bytes:
    return encode(obj, ROOTS[signal])


__all__: List[str] = ["DecodeError", "decode_request", "encode_request"]
