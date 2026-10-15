from rca.models import Severity
from rca.parsers import iter_range, parse_line, parse_ts


def test_text_line():
    e = parse_line("2026-10-06T10:00:00.250Z ERROR db: boom: bad", "db", 7)
    assert e and e.service == "db" and e.severity is Severity.ERROR
    assert e.message == "boom: bad" and e.pos == 7


def test_json_line_and_aliases():
    e = parse_line('{"ts":"2026-10-06T10:00:00Z","level":"critical","service":"x","msg":"m"}', "f")
    assert e and e.severity is Severity.FATAL and e.service == "x"


def test_epoch_timestamp():
    assert parse_ts(1_700_000_000) == 1_700_000_000.0


def test_garbage_never_raises():
    for bad in [
        "",
        "   ",
        "not a log",
        "{broken json",
        '{"msg": "no ts"}',
        "2026-99-99T00:00:00Z X y: z",
    ]:
        assert parse_line(bad, "f") is None


def test_ranges_cover_every_line_exactly_once(tmp_path):
    p = tmp_path / "f.log"
    lines = [f"line-{i}-{'x' * (i % 17)}" for i in range(500)]
    p.write_text("\n".join(lines) + "\n")
    size = p.stat().st_size
    for n in (1, 2, 3, 7, 50):
        bounds = [size * i // n for i in range(n)] + [size]
        got = [ln.strip() for i in range(n) for _, ln in iter_range(p, bounds[i], bounds[i + 1])]
        assert got == lines
