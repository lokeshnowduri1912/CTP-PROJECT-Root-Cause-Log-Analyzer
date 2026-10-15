"""End-to-end pipeline: load (parallel) -> merge (D&C) -> sequences -> correlate."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import perf_counter

from .correlate import Report, analyze
from .merge import merge_dc
from .models import LogEvent, event_key
from .parallel import DEFAULT_MIN_CHUNK, load_runs
from .patterns import DEFAULT_SEQUENCES, SequenceMatcher, SequencePattern

LOG_SUFFIXES = {".log", ".jsonl", ".json", ".txt"}


@dataclass(frozen=True)
class Timings:
    load_scan: float
    merge: float
    correlate: float

    @property
    def total(self) -> float:
        return self.load_scan + self.merge + self.correlate


@dataclass(frozen=True)
class PipelineResult:
    report: Report
    timings: Timings
    n_events: int
    n_sources: int
    workers: int
    events: Sequence[LogEvent] = field(default=(), repr=False)


def expand_paths(inputs: Sequence[str | Path]) -> list[Path]:
    out: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            out.extend(sorted(f for f in p.iterdir() if f.suffix in LOG_SUFFIXES))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(str(p))
    return out


def run_pipeline(
    inputs: Sequence[str | Path],
    *,
    workers: int | None = None,
    graph: Mapping[str, Sequence[str]] | None = None,
    lookback: float = 600.0,
    min_chunk: int = DEFAULT_MIN_CHUNK,
    patterns: Sequence[SequencePattern] = DEFAULT_SEQUENCES,
) -> PipelineResult:
    paths = expand_paths(inputs)
    if not paths:
        raise ValueError("no log files found")
    workers = workers or os.cpu_count() or 1

    t0 = perf_counter()
    runs = load_runs(paths, workers, min_chunk)  # stage 1: multiprocessing
    t1 = perf_counter()
    events = merge_dc(runs, event_key)  # stage 2: divide & conquer
    t2 = perf_counter()
    tagged = (e for e in events if e.signatures)  # sparse stream
    hits = SequenceMatcher(patterns).scan(tagged)  # stage 3: streaming NFA
    report = replace(analyze(events, graph, lookback=lookback), sequences=tuple(hits))
    t3 = perf_counter()
    return PipelineResult(
        report, Timings(t1 - t0, t2 - t1, t3 - t2), len(events), len(paths), workers, events
    )
