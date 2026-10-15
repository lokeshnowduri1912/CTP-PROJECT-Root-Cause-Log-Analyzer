import heapq
from itertools import chain

from hypothesis import given
from hypothesis import strategies as st

from rca.merge import merge_dc, merge_heap, merge_two

sorted_runs = st.lists(st.lists(st.integers(-50, 50)).map(sorted), max_size=12)


def ident(x):
    return x


def test_merge_two_basic():
    assert merge_two([1, 3, 5], [2, 4], ident) == [1, 2, 3, 4, 5]


def test_empty_inputs():
    assert merge_dc([], ident) == []
    assert merge_dc([[], []], ident) == []


@given(sorted_runs)
def test_merge_dc_equals_sorted_concat(runs):
    assert merge_dc(runs, ident) == sorted(chain.from_iterable(runs))


@given(sorted_runs)
def test_merge_dc_matches_heap_merge(runs):
    assert merge_dc(runs, ident) == list(merge_heap(runs, ident)) == list(heapq.merge(*runs))


@given(st.lists(st.lists(st.integers(0, 5)).map(sorted), max_size=8))
def test_merge_dc_is_stable(runs):
    tagged = [[(v, i) for v in run] for i, run in enumerate(runs)]
    got = merge_dc(tagged, lambda t: t[0])
    assert got == sorted(chain.from_iterable(tagged), key=lambda t: t[0])  # sorted() is stable


@given(sorted_runs)
def test_inputs_not_mutated(runs):
    snapshot = [list(r) for r in runs]
    merge_dc(runs, ident)
    assert runs == snapshot
