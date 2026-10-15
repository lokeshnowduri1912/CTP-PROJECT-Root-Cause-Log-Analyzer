import pytest

from rca.cli import main
from rca.gen_data import generate
from rca.pipeline import run_pipeline


@pytest.fixture(scope="module")
def logs(tmp_path_factory):
    d = tmp_path_factory.mktemp("logs")
    generate(d, noise=4000, seed=11)
    return d


def test_finds_injected_root_cause(logs):
    res = run_pipeline([logs], workers=1)
    assert res.report.root_cause.service == "db"
    assert res.report.failure.service == "gateway"
    assert any(h.pattern == "disk-full cascade" for h in res.report.sequences)


def test_parallel_equals_serial(logs):
    a = run_pipeline([logs], workers=1).report.to_dict(5)
    b = run_pipeline([logs], workers=3).report.to_dict(5)
    assert a == b


def test_byte_range_chunking_is_lossless(logs):
    whole = run_pipeline([logs], workers=1)
    chunked = run_pipeline([logs], workers=3, min_chunk=1500)  # forces many splits per file
    assert chunked.n_events == whole.n_events
    assert chunked.report.to_dict(5) == whole.report.to_dict(5)


def test_event_count_matches_line_count(logs):
    total = sum(len(p.read_text().splitlines()) for p in logs.iterdir())
    assert run_pipeline([logs], workers=2).n_events == total


def test_different_seeds_same_conclusion(tmp_path):
    for seed in (1, 2, 3):
        d = tmp_path / str(seed)
        generate(d, noise=1500, seed=seed)
        assert run_pipeline([d], workers=1).report.root_cause.service == "db"


def test_cli_json_end_to_end(logs, capsys):
    assert main(["analyze", str(logs), "--workers", "2", "--format", "json"]) == 0
    out = capsys.readouterr().out
    assert '"root_cause": "db"' in out


def test_cli_missing_path(capsys):
    assert main(["analyze", "/definitely/not/here"]) == 2


def test_text_report_mentions_root_cause(logs, capsys):
    assert main(["analyze", str(logs), "--workers", "1"]) == 0
    out = capsys.readouterr().out
    assert "ROOT CAUSE (most likely): db" in out and "Causal chain:" in out
    assert "disk-full cascade" in out


def test_text_report_without_failure(tmp_path, capsys):
    (tmp_path / "a.log").write_text("2026-10-06T10:00:00Z INFO web: fine\n")
    assert main(["analyze", str(tmp_path)]) == 0
    assert "No failure event" in capsys.readouterr().out


def test_custom_graph_file(logs, tmp_path, capsys):
    g = tmp_path / "g.json"
    g.write_text('{"gateway": ["orders"], "orders": ["db"]}')
    assert main(["analyze", str(logs), "--graph", str(g), "--format", "json"]) == 0
