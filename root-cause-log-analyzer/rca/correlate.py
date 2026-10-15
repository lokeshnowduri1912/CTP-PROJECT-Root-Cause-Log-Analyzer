"""Root-cause ranking.

Pipeline: pick the failure event -> slice the lookback window with binary search
-> group anomalies per service -> score each service with an explainable weighted
heuristic that combines time precedence, signature strength, severity and the
service dependency graph (greedy: prefer the most-upstream anomalous service).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .models import LogEvent, Severity
from .patterns import SIG_WEIGHTS, SequenceHit

# service -> services it depends on (its downstream providers)
DEFAULT_GRAPH: dict[str, list[str]] = {
    "gateway": ["orders", "payments"],
    "orders": ["db", "cache"],
    "payments": ["db"],
    "db": [],
    "cache": [],
}

W_EARLY, W_SIG, W_SEV, W_DEPTH, W_ROOT = 0.25, 0.35, 0.10, 0.05, 0.25


@dataclass(frozen=True)
class Candidate:
    service: str
    score: float
    first_ts: float
    last_ts: float
    count: int
    max_severity: Severity
    signatures: tuple[str, ...]
    evidence: tuple[LogEvent, ...]


@dataclass(frozen=True)
class Report:
    failure: LogEvent | None
    candidates: tuple[Candidate, ...]
    chain: tuple[str, ...]  # likely causal path: root -> ... -> failing service
    sequences: tuple[SequenceHit, ...] = ()
    n_events: int = 0

    @property
    def root_cause(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def to_dict(self, top: int = 3) -> dict[str, Any]:
        return {
            "failure": None
            if self.failure is None
            else {
                "ts": _iso(self.failure.ts),
                "service": self.failure.service,
                "message": self.failure.message,
            },
            "root_cause": None if self.root_cause is None else self.root_cause.service,
            "chain": list(self.chain),
            "candidates": [
                {
                    "service": c.service,
                    "score": round(c.score, 4),
                    "first_seen": _iso(c.first_ts),
                    "events": c.count,
                    "max_severity": c.max_severity.name,
                    "signatures": list(c.signatures),
                    "evidence": [
                        f"{_iso(e.ts)} [{e.severity.name}] {e.message}" for e in c.evidence
                    ],
                }
                for c in self.candidates[:top]
            ],
            "sequences": [
                {
                    "pattern": h.pattern,
                    "services": [e.service for e in h.events],
                    "start": _iso(h.events[0].ts),
                    "end": _iso(h.events[-1].ts),
                }
                for h in self.sequences
            ],
        }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds")


def reachable(graph: Mapping[str, Sequence[str]], start: str) -> dict[str, int]:
    """BFS distances from `start` over the dependency graph (start -> 0)."""
    dist = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        u = queue.popleft()
        for v in graph.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def find_failure(
    events: Sequence[LogEvent], min_severity: Severity = Severity.ERROR
) -> LogEvent | None:
    """First event of the highest severity seen (>= min_severity)."""
    if not events:
        return None
    top = max(e.severity for e in events)
    if top < min_severity:
        return None
    return next(e for e in events if e.severity == top)


def analyze(
    events: Sequence[LogEvent],
    graph: Mapping[str, Sequence[str]] | None = None,
    *,
    failure: LogEvent | None = None,
    lookback: float = 600.0,
    min_severity: Severity = Severity.WARN,
    sequences: Sequence[SequenceHit] = (),
) -> Report:
    """`events` must be globally time-sorted (output of the divide-and-conquer merge)."""
    graph = DEFAULT_GRAPH if graph is None else graph
    failure = failure or find_failure(events)
    if failure is None:
        return Report(None, (), (), tuple(sequences), len(events))

    times = [e.ts for e in events]  # O(N) once, then O(log N) slices
    lo = bisect_left(times, failure.ts - lookback)
    hi = bisect_right(times, failure.ts)
    anomalous = [e for e in events[lo:hi] if e.severity >= min_severity or e.signatures]
    if not anomalous:
        return Report(failure, (), (), tuple(sequences), len(events))

    groups: dict[str, list[LogEvent]] = {}
    for e in anomalous:
        groups.setdefault(e.service, []).append(e)

    dist = reachable(graph, failure.service)
    max_dist = max(dist.values()) or 1
    t_min = anomalous[0].ts
    span = max(failure.ts - t_min, 1e-9)

    candidates: list[Candidate] = []
    for svc, evs in groups.items():
        sigs = sorted({s for e in evs for s in e.signatures})
        sig_score = min(1.0, sum(SIG_WEIGHTS.get(s, 0.3) for s in sigs) / 2.0)
        sev = max(e.severity for e in evs)
        early = 1.0 - (evs[0].ts - t_min) / span
        depth = dist.get(svc, 0) / max_dist
        upstream_anomalies = (set(reachable(graph, svc)) - {svc}) & groups.keys()
        root = 0.0 if upstream_anomalies else 1.0
        score = (
            W_EARLY * early
            + W_SIG * sig_score
            + W_SEV * int(sev) / int(Severity.FATAL)
            + W_DEPTH * depth
            + W_ROOT * root
        )
        top_evidence = sorted(evs, key=lambda e: (-int(e.severity), e.ts))[:5]
        candidates.append(
            Candidate(
                svc,
                score,
                evs[0].ts,
                evs[-1].ts,
                len(evs),
                sev,
                tuple(sigs),
                tuple(sorted(top_evidence, key=lambda e: e.ts)),
            )
        )
    candidates.sort(key=lambda c: (-c.score, c.first_ts, c.service))

    chain = sorted(
        (s for s in groups if s in dist and s != failure.service), key=lambda s: groups[s][0].ts
    )
    chain.append(failure.service)  # the symptom is always last
    return Report(failure, tuple(candidates), tuple(chain), tuple(sequences), len(events))
