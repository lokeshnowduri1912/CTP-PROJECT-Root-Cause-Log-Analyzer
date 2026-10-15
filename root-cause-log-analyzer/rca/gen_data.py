"""Synthetic multi-source log generator with an injected, known root cause.

Scenario: the `db` host runs out of disk -> connections pile up -> orders/payments
time out -> gateway opens its circuit breaker and goes FATAL. A noisy `cache`
warning appears even earlier as a decoy. Ground truth: root cause == "db".
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from .parsers import parse_ts

FORMATS = {"gateway": "text", "orders": "json", "payments": "json", "db": "text", "cache": "text"}
_NOISE = {
    "gateway": ["GET /api/items 200 12ms", "POST /api/cart 201 31ms", "health check ok"],
    "orders": ["order 8841 created", "order 8842 shipped", "inventory sync complete"],
    "payments": ["charge 1203 authorised", "refund 77 processed", "webhook delivered"],
    "db": ["checkpoint complete", "autovacuum finished", "connection accepted"],
    "cache": ["cache hit ratio 0.93", "evicted 12 keys", "replica in sync"],
}


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _line(kind: str, ts: float, sev: str, svc: str, msg: str) -> str:
    if kind == "json":
        return json.dumps({"ts": _fmt(ts), "level": sev, "service": svc, "msg": msg})
    return f"{_fmt(ts)} {sev} {svc}: {msg}"


def generate(
    out_dir: str | Path, noise: int = 2000, seed: int = 7, start: str = "2026-10-06T10:00:00Z"
) -> list[Path]:
    rng = random.Random(seed)
    t0 = parse_ts(start)
    fail = t0 + 1800.0
    rows: dict[str, list[tuple[float, str]]] = {s: [] for s in FORMATS}

    def add(svc: str, ts: float, sev: str, msg: str) -> None:
        # +-0.4s jitter in *write order* mimics slightly out-of-order log shippers
        rows[svc].append((ts + rng.uniform(-0.4, 0.4), _line(FORMATS[svc], ts, sev, svc, msg)))

    for _ in range(noise):
        svc = rng.choice(list(FORMATS))
        sev = rng.choices(["DEBUG", "INFO", "WARN"], [2, 25, 1])[0]
        msg = (
            "slow request 850ms" if sev == "WARN" and svc == "gateway" else rng.choice(_NOISE[svc])
        )
        if sev == "WARN" and svc != "gateway":
            sev = "INFO"
        add(svc, t0 + rng.random() * 3600.0, sev, msg)

    scenario = [
        ("cache", -500, "WARN", "slow response 250ms from replica"),  # decoy
        ("orders", -900, "INFO", "Deploying build 4821"),  # outside lookback
        ("db", -240, "WARN", "disk usage at 96% on /var/lib/db"),
        ("db", -200, "ERROR", "write failed: No space left on device"),
        ("db", -150, "ERROR", "FATAL: Too many connections for role app"),
        ("orders", -120, "WARN", "query timed out after 5000ms"),
        ("payments", -100, "WARN", "db request timed out"),
        ("orders", -90, "ERROR", "Connection refused by db:5432"),
        ("gateway", -60, "ERROR", "upstream orders timed out"),
        ("gateway", -30, "ERROR", "upstream payments deadline exceeded"),
        ("gateway", 0, "FATAL", "service unavailable: circuit breaker open for orders"),
    ]
    for svc, off, sev, msg in scenario:
        add(svc, fail + off, sev, msg)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for svc, items in rows.items():
        items.sort(key=lambda r: r[0])
        p = out / f"{svc}.{'jsonl' if FORMATS[svc] == 'json' else 'log'}"
        p.write_text("\n".join(line for _, line in items) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
