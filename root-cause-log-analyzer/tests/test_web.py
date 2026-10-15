import json
import threading
import time
import urllib.request
from pathlib import Path

from rca.gen_data import generate
from rca.web import Analyzer, Replayer, create_server


def test_analyzer_finds_db(tmp_path: Path) -> None:
    generate(tmp_path, 300, 7)
    a = Analyzer(tmp_path)
    assert a.refresh()
    assert a.state["root_cause"] == "db"
    assert a.state["timeline"]["rows"] and a.state["tail"] and a.state["errors"] > 0
    assert not a.refresh()  # unchanged directory -> no recompute
    a.reset()
    assert a.state["status"] == "waiting"


def test_analyzer_empty_dir(tmp_path: Path) -> None:
    assert not Analyzer(tmp_path).refresh()
    assert not Analyzer(tmp_path / "missing").refresh()


def test_replayer_streams_incident(tmp_path: Path) -> None:
    a = Analyzer(tmp_path)
    r = Replayer(tmp_path, a, noise=60, tick=0)
    r.start()
    deadline = time.time() + 10
    while time.time() < deadline and not (r.total and r.emitted == r.total):
        time.sleep(0.05)
    assert r.emitted == r.total > 0
    assert a.refresh() or a.state["status"] == "ok"
    assert a.state["root_cause"] == "db"


def test_http_endpoints(tmp_path: Path) -> None:
    generate(tmp_path, 100, 7)
    httpd, _, _ = create_server(tmp_path, port=0, simulate=False)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert b"Root-Cause" in urllib.request.urlopen(base + "/").read()
        state = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert state["root_cause"] == "db" and state["replay"] is None
        with urllib.request.urlopen(base + "/events") as resp:
            assert resp.readline().startswith(b"data: ")
        req = urllib.request.Request(base + "/api/restart", method="POST")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        httpd.shutdown()
