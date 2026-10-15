"""Command line interface:  python -m rca analyze|generate ..."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .gen_data import generate
from .pipeline import run_pipeline
from .report import render_text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rca", description="Root-Cause Log Analyzer")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="correlate logs and rank root-cause candidates")
    a.add_argument("paths", nargs="+", help="log files and/or directories")
    a.add_argument("--workers", type=int, default=None, help="processes (default: CPU count)")
    a.add_argument("--lookback", type=float, default=600.0, help="seconds before the failure")
    a.add_argument("--graph", help="JSON file: {service: [dependencies...]}")
    a.add_argument("--format", choices=("text", "json"), default="text")
    a.add_argument("--top", type=int, default=3)

    g = sub.add_parser("generate", help="write a synthetic incident into a directory")
    g.add_argument("out_dir")
    g.add_argument("--noise", type=int, default=2000)
    g.add_argument("--seed", type=int, default=7)

    w = sub.add_parser("serve", help="real-time web dashboard")
    w.add_argument("--logs", help="watch this log directory (default: simulate a live incident)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)
    w.add_argument("--noise", type=int, default=3000, help="simulated noise lines")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "generate":
        for path in generate(args.out_dir, args.noise, args.seed):
            print(path)
        return 0

    if args.cmd == "serve":  # pragma: no cover
        from pathlib import Path

        from .web import serve

        serve(Path(args.logs or "live_logs"), args.host, args.port, args.logs is None, args.noise)
        return 0

    graph = None
    if args.graph:
        with open(args.graph, encoding="utf-8") as fh:
            graph = json.load(fh)
    try:
        res = run_pipeline(args.paths, workers=args.workers, graph=graph, lookback=args.lookback)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(res.report.to_dict(args.top), indent=2))
    else:
        print(render_text(res, args.top))
    return 0
