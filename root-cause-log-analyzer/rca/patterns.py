"""Streaming pattern matching.

1. AhoCorasick      - multi-keyword matcher, one pass over the text: O(|text| + matches)
2. SignatureMatcher - tags each event with failure signatures (OOM, DISK_FULL, ...)
3. SequenceMatcher  - bounded-state NFA over the event stream, detecting ordered
                      cascades (A -> B -> C) inside a time window.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from .models import LogEvent


class AhoCorasick:
    """Aho-Corasick automaton (case-insensitive). Build O(sum |keywords|)."""

    def __init__(self, keywords: Mapping[str, str]) -> None:
        self._goto: list[dict[str, int]] = [{}]
        self._fail: list[int] = [0]
        self._out: list[list[str]] = [[]]
        for kw, label in keywords.items():
            node = 0
            for ch in kw.lower():
                nxt = self._goto[node].get(ch)
                if nxt is None:
                    self._goto.append({})
                    self._fail.append(0)
                    self._out.append([])
                    nxt = len(self._goto) - 1
                    self._goto[node][ch] = nxt
                node = nxt
            self._out[node].append(label)
        queue: deque[int] = deque(self._goto[0].values())
        while queue:  # BFS builds failure links
            u = queue.popleft()
            for ch, v in self._goto[u].items():
                f = self._fail[u]
                while f and ch not in self._goto[f]:
                    f = self._fail[f]
                self._fail[v] = self._goto[f].get(ch, 0)
                self._out[v] = self._out[v] + self._out[self._fail[v]]
                queue.append(v)

    def search(self, text: str) -> Iterator[tuple[int, str]]:
        """Yield (end_index, label) for every keyword occurrence, streaming."""
        node = 0
        for i, ch in enumerate(text.lower()):
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            for label in self._out[node]:
                yield i, label


@dataclass(frozen=True)
class Signature:
    name: str
    keywords: tuple[str, ...]
    weight: float  # how strongly this indicates a *cause* (0..1)


DEFAULT_SIGNATURES: tuple[Signature, ...] = (
    Signature("DISK_FULL", ("no space left on device", "disk full"), 0.9),
    Signature("OOM", ("out of memory", "oomkilled", "oom-kill"), 0.9),
    Signature("CRASH", ("segmentation fault", "panic:", "unhandled exception"), 0.85),
    Signature("NET", ("network unreachable", "no route to host", "dns resolution failed"), 0.8),
    Signature(
        "DB_CONN", ("too many connections", "connection refused", "connection pool exhausted"), 0.8
    ),
    Signature("DEPLOY", ("deploying build", "rolling update", "config reloaded"), 0.5),
    Signature("TIMEOUT", ("timed out", "deadline exceeded"), 0.5),
    Signature("MEM_PRESSURE", ("memory usage at", "gc overhead"), 0.4),
    Signature("CIRCUIT", ("circuit breaker open",), 0.3),
)
SIG_WEIGHTS: dict[str, float] = {s.name: s.weight for s in DEFAULT_SIGNATURES}


class SignatureMatcher:
    """Tags messages with signature names using one Aho-Corasick pass."""

    def __init__(self, signatures: Iterable[Signature] = DEFAULT_SIGNATURES) -> None:
        mapping = {kw: s.name for s in signatures for kw in s.keywords}
        self._ac = AhoCorasick(mapping)

    def match(self, message: str) -> tuple[str, ...]:
        return tuple(sorted({label for _, label in self._ac.search(message)}))


@dataclass(frozen=True)
class SequencePattern:
    name: str
    steps: tuple[str, ...]  # signature names in required order
    window: float  # max seconds between first and last step

    def __post_init__(self) -> None:
        if len(self.steps) < 2:
            raise ValueError("a sequence needs at least two steps")


DEFAULT_SEQUENCES: tuple[SequencePattern, ...] = (
    SequencePattern("disk-full cascade", ("DISK_FULL", "DB_CONN", "TIMEOUT"), 600.0),
    SequencePattern("bad-deploy cascade", ("DEPLOY", "CRASH", "TIMEOUT"), 600.0),
    SequencePattern("memory exhaustion", ("MEM_PRESSURE", "OOM"), 900.0),
)


@dataclass(frozen=True)
class SequenceHit:
    pattern: str
    events: tuple[LogEvent, ...]


class SequenceMatcher:
    """Streaming NFA, skip-till-next-match semantics.

    State per pattern is a list of partial chains, expired by the time window and
    capped, so memory is O(patterns * cap) regardless of stream length.
    """

    def __init__(
        self, patterns: Sequence[SequencePattern] = DEFAULT_SEQUENCES, cap: int = 256
    ) -> None:
        self._patterns = tuple(patterns)
        self._cap = cap
        self._partials: dict[str, list[list[LogEvent]]] = {p.name: [] for p in patterns}

    def feed(self, event: LogEvent) -> list[SequenceHit]:
        hits: list[SequenceHit] = []
        if not event.signatures:
            return hits
        sigs = set(event.signatures)
        for p in self._patterns:
            live = [c for c in self._partials[p.name] if event.ts - c[0].ts <= p.window]
            nxt: list[list[LogEvent]] = []
            for chain in live:
                if p.steps[len(chain)] in sigs:
                    ext = [*chain, event]
                    if len(ext) == len(p.steps):
                        hits.append(SequenceHit(p.name, tuple(ext)))
                    else:
                        nxt.append(ext)
                else:
                    nxt.append(chain)
            if p.steps[0] in sigs:
                nxt.append([event])
            self._partials[p.name] = nxt[-self._cap :]
        return hits

    def scan(self, events: Iterable[LogEvent]) -> list[SequenceHit]:
        out: list[SequenceHit] = []
        for e in events:
            out.extend(self.feed(e))
        return out
