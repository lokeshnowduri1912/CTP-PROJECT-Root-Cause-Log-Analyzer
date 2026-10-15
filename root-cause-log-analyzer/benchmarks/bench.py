"""Performance evaluation.   python benchmarks/bench.py [--noise 300000]

Part A: merge strategies on k sorted runs.      Part B: speed-up vs. worker count.
"""

from __future__ import annotations

import argparse
import heapq
import random
import tempfile
from itertools import chain
from time import perf_counter

from rca.gen_data import generate
from rca.merge import merge_dc
from rca.pipeline import run_pipeline


def timeit(fn):
    t = perf_counter()
    fn()
    return perf_counter() - t


def bench_merge(k: int = 64, n: int = 20_000) -> None:
    rng = random.Random(1)
    runs = [sorted(rng.random() for _ in range(n)) for _ in range(k)]
    ident = lambda x: x  # noqa: E731
    print(f"Part A: merge {k} sorted runs x {n:,} items")
    print(f"  sorted(concat)    {timeit(lambda: sorted(chain.from_iterable(runs))):.3f}s")
    print(f"  heapq.merge       {timeit(lambda: list(heapq.merge(*runs))):.3f}s")
    print(f"  merge_dc (ours)   {timeit(lambda: merge_dc(runs, ident)):.3f}s")


def bench_parallel(noise: int) -> None:
    with tempfile.TemporaryDirectory() as d:
        generate(d, noise=noise)
        print(f"\nPart B: end-to-end, {noise:,} noise lines")
        base = None
        for w in (1, 2, 4, 8):
            r = run_pipeline([d], workers=w, min_chunk=256 * 1024)
            base = base or r.timings.total
            print(
                f"  workers={w}  total={r.timings.total:.3f}s  "
                f"load+scan={r.timings.load_scan:.3f}s  speed-up x{base / r.timings.total:.2f}"
            )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--noise", type=int, default=300_000)
    args = ap.parse_args()
    bench_merge()
    bench_parallel(args.noise)
