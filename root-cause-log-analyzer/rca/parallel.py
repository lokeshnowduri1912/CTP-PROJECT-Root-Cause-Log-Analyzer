"""Multiprocessing stage: parse + signature-scan byte-range chunks in parallel.

Why processes? Parsing and regex/automaton matching are CPU-bound pure-Python work,
so threads would serialise on the GIL. Each worker is independent (no shared state),
returns a time-sorted run, and the parent merges runs with divide-and-conquer.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

from .models import LogEvent, event_key
from .parsers import iter_range, parse_line
from .patterns import SignatureMatcher

DEFAULT_MIN_CHUNK = 4 * 1024 * 1024  # don't split files smaller than this
Task = tuple[str, int, int]  # (path, start_byte, end_byte)

_MATCHER: SignatureMatcher | None = None


def _init_worker() -> None:
    """Build the automaton once per worker process (not once per task)."""
    global _MATCHER
    _MATCHER = SignatureMatcher()


def load_range(
    path: str, start: int, end: int, matcher: SignatureMatcher | None = None
) -> list[LogEvent]:
    """Parse + tag one byte range and return it sorted by time."""
    matcher = matcher or _MATCHER or SignatureMatcher()
    source = Path(path).stem
    out: list[LogEvent] = []
    for pos, line in iter_range(path, start, end):
        ev = parse_line(line, source, pos)
        if ev is None:
            continue
        sigs = matcher.match(ev.message)
        out.append(replace(ev, signatures=sigs) if sigs else ev)
    out.sort(key=event_key)  # Timsort is O(n) on nearly-sorted logs
    return out


def _run_task(task: Task) -> list[LogEvent]:
    return load_range(*task)


def plan_tasks(
    paths: Iterable[str | Path], workers: int, min_chunk: int = DEFAULT_MIN_CHUNK
) -> list[Task]:
    """Split big files into newline-aligned byte ranges; keep small files whole."""
    tasks: list[Task] = []
    for p in paths:
        size = os.path.getsize(p)
        n = max(1, min(workers, size // max(1, min_chunk)))
        bounds = [size * i // n for i in range(n)] + [size]
        tasks.extend((str(p), bounds[i], bounds[i + 1]) for i in range(n))
    return tasks


def load_runs(
    paths: Sequence[str | Path], workers: int, min_chunk: int = DEFAULT_MIN_CHUNK
) -> list[list[LogEvent]]:
    tasks = plan_tasks(paths, workers, min_chunk)
    if workers <= 1 or len(tasks) == 1:
        matcher = SignatureMatcher()
        return [load_range(p, s, e, matcher) for p, s, e in tasks]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
        return list(pool.map(_run_task, tasks))  # map preserves task order => deterministic
