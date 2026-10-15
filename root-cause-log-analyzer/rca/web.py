"""Real-time dashboard: stdlib HTTP server + Server-Sent Events (no third-party deps).

    python -m rca serve                 # simulate a live incident, open http://127.0.0.1:8000
    python -m rca serve --logs /my/logs # watch a real log directory instead
"""

from __future__ import annotations

import json
import math
import tempfile
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .gen_data import generate
from .models import LogEvent
from .parsers import parse_ts
from .pipeline import LOG_SUFFIXES, run_pipeline

INDEX = Path(__file__).with_name("static") / "index.html"
_DRAMATIC = ("ERROR", "FATAL", "disk usage", "timed out", "slow response", "Deploying")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds")


def _extras(events: Sequence[LogEvent], buckets: int = 48) -> dict[str, Any]:
    """Heat-map grid (worst severity per service per time bucket) + newest log lines."""
    t0, t1 = events[0].ts, events[-1].ts
    span = max(t1 - t0, 1.0)
    rows = {s: [0] * buckets for s in sorted({e.service for e in events})}
    for e in events:
        i = min(buckets - 1, int((e.ts - t0) / span * buckets))
        rows[e.service][i] = max(rows[e.service][i], min(4, max(1, int(e.severity) // 10 - 1)))
    return {
        "timeline": {"start": _iso(t0), "end": _iso(t1), "rows": rows},
        "tail": [
            {"ts": _iso(e.ts), "svc": e.service, "sev": e.severity.name, "msg": e.message}
            for e in events[-40:]
        ],
        "errors": sum(e.severity >= 40 for e in events),
        "warns": sum(e.severity == 30 for e in events),
    }


class Analyzer:
    """Re-runs the pipeline whenever the log directory grows; caches the latest result."""

    def __init__(self, log_dir: Path, top: int = 4) -> None:
        self.log_dir = log_dir
        self.top = top
        self.state: dict[str, Any] = {"status": "waiting"}
        self.version = 0
        self._sig: tuple[tuple[str, int], ...] = ()

    def _signature(self) -> tuple[tuple[str, int], ...]:
        if not self.log_dir.is_dir():
            return ()
        files = sorted(p for p in self.log_dir.iterdir() if p.suffix in LOG_SUFFIXES)
        return tuple((p.name, p.stat().st_size) for p in files)

    def reset(self) -> None:
        self._sig = ()
        self.state = {"status": "waiting"}
        self.version += 1

    def refresh(self) -> bool:
        sig = self._signature()
        if sig == self._sig:
            return False
        self._sig = sig
        try:
            res = run_pipeline([self.log_dir], workers=1)
        except (ValueError, FileNotFoundError):
            return False
        payload = res.report.to_dict(self.top)
        payload.update(
            status="ok",
            events=res.n_events,
            sources=res.n_sources,
            analysis_ms=round(res.timings.total * 1000, 1),
            **_extras(res.events),
        )
        self.state = payload
        self.version += 1
        return True


def _line_ts(line: str) -> float:
    if line.startswith("{"):
        return parse_ts(json.loads(line)["ts"])
    return parse_ts(line.split(" ", 1)[0])


class Replayer(threading.Thread):
    """Replays a synthetic incident into `out_dir` line by line, like services logging live."""

    def __init__(
        self, out_dir: Path, analyzer: Analyzer, noise: int = 3000, seed: int = 7, tick: float = 0.6
    ) -> None:
        super().__init__(daemon=True)
        self.out_dir, self.analyzer, self.noise, self.seed, self.tick = (
            out_dir,
            analyzer,
            noise,
            seed,
            tick,
        )
        self.emitted = 0
        self.total = 0
        self.running = False
        self._restart = threading.Event()

    def restart(self) -> None:
        self._restart.set()

    def _steps(self) -> list[list[tuple[str, str]]]:
        with tempfile.TemporaryDirectory() as tmp:
            rows = [
                (_line_ts(ln), p.name, ln)
                for p in generate(tmp, self.noise, self.seed)
                for ln in p.read_text(encoding="utf-8").splitlines()
            ]
        rows.sort(key=lambda r: r[0])
        self.total = len(rows)
        size = max(1, math.ceil(len(rows) / 30))
        steps: list[list[tuple[str, str]]] = []
        buf: list[tuple[str, str]] = []
        for _, name, line in rows:
            if any(k in line for k in _DRAMATIC):  # incident lines arrive one at a time
                if buf:
                    steps.append(buf)
                    buf = []
                steps.append([(name, line)])
            else:
                buf.append((name, line))
                if len(buf) >= size:
                    steps.append(buf)
                    buf = []
        if buf:
            steps.append(buf)
        return steps

    def _clear(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for p in self.out_dir.iterdir():
            if p.suffix in LOG_SUFFIXES:
                p.unlink(missing_ok=True)
        self.analyzer.reset()

    def run(self) -> None:
        steps = self._steps()
        while True:
            self._restart.clear()
            self._clear()
            self.emitted, self.running = 0, True
            for step in steps:
                if self._restart.is_set():
                    break
                by_file: dict[str, list[str]] = {}
                for name, line in step:
                    by_file.setdefault(name, []).append(line)
                for name, lines in by_file.items():
                    with (self.out_dir / name).open("a", encoding="utf-8") as fh:
                        fh.write("\n".join(lines) + "\n")
                self.emitted += len(step)
                time.sleep(self.tick)
            self.running = False
            self._restart.wait()


def snapshot(analyzer: Analyzer, replayer: Replayer | None) -> dict[str, Any]:
    out = dict(analyzer.state)
    out["replay"] = (
        None
        if replayer is None
        else {"emitted": replayer.emitted, "total": replayer.total, "running": replayer.running}
    )
    return out


def make_handler(analyzer: Analyzer, replayer: Replayer | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                body = json.dumps(snapshot(analyzer, replayer)).encode()
                self._send(200, body, "application/json")
            elif self.path == "/events":
                self._stream()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if self.path == "/api/restart" and replayer is not None:
                replayer.restart()
                self._send(200, b"{}", "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last, idle = "", 0.0
            try:
                while True:
                    data = json.dumps(snapshot(analyzer, replayer))
                    if data != last:
                        self.wfile.write(f"data: {data}\n\n".encode())
                        last, idle = data, 0.0
                    elif idle >= 15:
                        self.wfile.write(b": keep-alive\n\n")
                        idle = 0.0
                    self.wfile.flush()
                    time.sleep(0.4)
                    idle += 0.4
            except OSError:  # client closed the tab
                return

    return Handler


def create_server(
    log_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8000,
    simulate: bool = True,
    noise: int = 3000,
) -> tuple[ThreadingHTTPServer, Analyzer, Replayer | None]:
    analyzer = Analyzer(log_dir)
    replayer = Replayer(log_dir, analyzer, noise) if simulate else None
    analyzer.refresh()
    httpd = ThreadingHTTPServer((host, port), make_handler(analyzer, replayer))
    httpd.daemon_threads = True
    return httpd, analyzer, replayer


def serve(
    log_dir: Path, host: str, port: int, simulate: bool, noise: int
) -> None:  # pragma: no cover
    httpd, analyzer, replayer = create_server(log_dir, host, port, simulate, noise)

    def watch() -> None:
        while True:
            analyzer.refresh()
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    if replayer:
        replayer.start()
    mode = "simulating a live incident" if simulate else f"watching {log_dir}"
    print(f"RCA dashboard ({mode}): http://{host}:{port}   (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
