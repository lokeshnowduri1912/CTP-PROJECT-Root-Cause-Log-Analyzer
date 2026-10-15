"""Core domain model: immutable log events and severities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Severity(IntEnum):
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50

    @classmethod
    def parse(cls, text: str) -> Severity:
        aliases = {
            "WARNING": "WARN",
            "CRITICAL": "FATAL",
            "CRIT": "FATAL",
            "ERR": "ERROR",
            "TRACE": "DEBUG",
            "NOTICE": "INFO",
        }
        name = text.strip().upper()
        name = aliases.get(name, name)
        try:
            return cls[name]
        except KeyError:
            return cls.INFO


@dataclass(frozen=True, slots=True)
class LogEvent:
    """One normalised log record. Immutable => safe to share across processes."""

    ts: float  # epoch seconds (UTC)
    source: str  # originating file / stream
    service: str  # logical service that emitted it
    severity: Severity
    message: str
    pos: int = 0  # byte offset in source (deterministic tie-break)
    signatures: tuple[str, ...] = ()  # filled by the streaming pattern matcher


def event_key(e: LogEvent) -> tuple[float, str, int]:
    """Total order on events: time, then source, then position => deterministic merges."""
    return (e.ts, e.source, e.pos)
