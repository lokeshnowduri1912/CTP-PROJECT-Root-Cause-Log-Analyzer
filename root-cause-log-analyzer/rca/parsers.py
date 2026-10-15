"""Line parsers for heterogeneous log formats (text and JSON-lines)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .models import LogEvent, Severity

_TEXT = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)\s+"
    r"(?P<lvl>[A-Za-z]+)\s+(?P<svc>[\w.\-]+):\s?(?P<msg>.*)$"
)


def parse_ts(value: str | float | int) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def parse_line(line: str, source: str, pos: int = 0) -> LogEvent | None:
    """Parse one line; return None for blank/garbled lines (never raises)."""
    line = line.strip()
    if not line:
        return None
    try:
        if line.startswith("{"):
            d = json.loads(line)
            ts = parse_ts(d.get("ts") or d.get("time") or d["timestamp"])
            sev = Severity.parse(str(d.get("level") or d.get("severity") or "INFO"))
            svc = str(d.get("service") or d.get("svc") or source)
            msg = str(d.get("msg") or d.get("message") or "")
            return LogEvent(ts, source, svc, sev, msg, pos)
        m = _TEXT.match(line)
        if m is None:
            return None
        return LogEvent(
            parse_ts(m["ts"]), source, m["svc"], Severity.parse(m["lvl"]), m["msg"], pos
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def iter_range(path: str | Path, start: int, end: int) -> Iterator[tuple[int, str]]:
    """Stream (offset, line) for every line that *starts* in [start, end).

    Newline-aligned so that byte-range chunks of one file can be parsed by
    different processes without losing or duplicating a line.
    """
    with open(path, "rb") as f:
        if start > 0:
            f.seek(start - 1)
            f.readline()  # skip the line that began before `start`
        while True:
            pos = f.tell()
            if pos >= end:
                break
            raw = f.readline()
            if not raw:
                break
            yield pos, raw.decode("utf-8", errors="replace")
