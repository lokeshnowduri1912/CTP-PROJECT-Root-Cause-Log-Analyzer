import pytest
from hypothesis import given
from hypothesis import strategies as st

from rca.models import LogEvent, Severity
from rca.patterns import (
    AhoCorasick,
    SequenceMatcher,
    SequencePattern,
    SignatureMatcher,
)

text = st.text(alphabet="abc", max_size=40)
kws = st.lists(st.text(alphabet="abc", min_size=1, max_size=4), min_size=1, max_size=6)


def naive(keywords, s):
    out = []
    for kw in set(keywords):
        i = s.find(kw)
        while i != -1:
            out.append((i + len(kw) - 1, kw))
            i = s.find(kw, i + 1)
    return sorted(out)


@given(kws, text)
def test_aho_corasick_equals_naive(keywords, s):
    ac = AhoCorasick({k: k for k in keywords})
    assert sorted(ac.search(s)) == naive(keywords, s)


def test_overlapping_and_case_insensitive():
    ac = AhoCorasick({"he": "HE", "she": "SHE", "hers": "HERS"})
    assert sorted(ac.search("USHERS")) == [(3, "HE"), (3, "SHE"), (5, "HERS")]


def test_signature_matcher():
    m = SignatureMatcher()
    assert m.match("write failed: No space left on device") == ("DISK_FULL",)
    assert m.match("all good") == ()
    assert set(m.match("OOMKilled after request timed out")) == {"OOM", "TIMEOUT"}


def ev(ts, *sigs, svc="s"):
    return LogEvent(ts, "src", svc, Severity.ERROR, "m", 0, tuple(sigs))


def test_sequence_detected_in_window():
    sm = SequenceMatcher([SequencePattern("p", ("A", "B", "C"), 10.0)])
    hits = sm.scan([ev(0, "A"), ev(3, "X"), ev(5, "B"), ev(9, "C")])
    assert len(hits) == 1 and [e.ts for e in hits[0].events] == [0, 5, 9]


def test_sequence_expires_outside_window():
    sm = SequenceMatcher([SequencePattern("p", ("A", "B"), 5.0)])
    assert sm.scan([ev(0, "A"), ev(6, "B")]) == []


def test_sequence_order_matters():
    sm = SequenceMatcher([SequencePattern("p", ("A", "B"), 5.0)])
    assert sm.scan([ev(0, "B"), ev(1, "A")]) == []


def test_sequence_state_is_bounded():
    sm = SequenceMatcher([SequencePattern("p", ("A", "B"), 1e9)], cap=10)
    sm.scan([ev(i, "A") for i in range(1000)])
    assert len(sm._partials["p"]) <= 10


def test_pattern_needs_two_steps():
    with pytest.raises(ValueError):
        SequencePattern("bad", ("A",), 1.0)
