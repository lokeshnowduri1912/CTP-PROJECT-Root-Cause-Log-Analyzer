"""Divide-and-conquer merge of already-sorted timelines.

merge_dc  : recursively split the k runs in half, merge the halves  -> O(N log k)
merge_heap: streaming k-way merge with a heap (lazy, O(k) memory)   -> O(N log k)
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, TypeVar

T = TypeVar("T")
KeyFn = Callable[[T], Any]


def merge_two(a: Sequence[T], b: Sequence[T], key: KeyFn[T]) -> list[T]:
    """Stable linear merge: on ties the element of `a` comes first. O(|a|+|b|)."""
    out: list[T] = []
    i = j = 0
    na, nb = len(a), len(b)
    while i < na and j < nb:
        if key(b[j]) < key(a[i]):
            out.append(b[j])
            j += 1
        else:
            out.append(a[i])
            i += 1
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def merge_dc(runs: Sequence[Sequence[T]], key: KeyFn[T]) -> list[T]:
    """Divide-and-conquer merge of k sorted runs.

    T(k) = 2T(k/2) + O(N)  =>  O(N log k) time, O(N) extra space, recursion depth log k.
    Stable with respect to run order.
    """
    if not runs:
        return []

    def rec(lo: int, hi: int) -> list[T]:
        if hi - lo == 1:
            return list(runs[lo])
        mid = (lo + hi) // 2
        return merge_two(rec(lo, mid), rec(mid, hi), key)

    return rec(0, len(runs))


def merge_heap(streams: Iterable[Iterable[T]], key: KeyFn[T]) -> Iterator[T]:
    """Lazy k-way merge (constant memory per stream) for true streaming inputs."""
    return heapq.merge(*streams, key=key)
