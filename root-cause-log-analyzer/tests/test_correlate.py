from rca.correlate import analyze, find_failure, reachable
from rca.models import LogEvent, Severity

G = {"web": ["api"], "api": ["db"], "db": []}


def e(ts, svc, sev, *sigs):
    return LogEvent(float(ts), "s", svc, sev, "m", 0, tuple(sigs))


def test_reachable_distances():
    assert reachable(G, "web") == {"web": 0, "api": 1, "db": 2}


def test_no_failure_returns_empty_report():
    r = analyze([e(1, "web", Severity.INFO)], G)
    assert r.failure is None and r.root_cause is None


def test_most_upstream_with_strong_signature_wins():
    events = [
        e(0, "db", Severity.ERROR, "DISK_FULL"),
        e(5, "api", Severity.WARN, "TIMEOUT"),
        e(10, "web", Severity.FATAL),
    ]
    r = analyze(events, G, lookback=60)
    assert r.root_cause.service == "db"
    assert r.chain == ("db", "api", "web")


def test_events_outside_lookback_ignored():
    events = [
        e(0, "db", Severity.ERROR, "DISK_FULL"),  # too old
        e(1000, "api", Severity.WARN, "TIMEOUT"),
        e(1005, "web", Severity.FATAL),
    ]
    r = analyze(events, G, lookback=60)
    assert "db" not in {c.service for c in r.candidates}


def test_find_failure_picks_first_of_top_severity():
    evs = [e(1, "a", Severity.ERROR), e(2, "b", Severity.FATAL), e(3, "c", Severity.FATAL)]
    assert find_failure(evs).service == "b"
