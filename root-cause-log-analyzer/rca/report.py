"""Human-readable rendering of a PipelineResult."""

from __future__ import annotations

from datetime import UTC, datetime

from .pipeline import PipelineResult


def _t(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%H:%M:%S")


def render_text(res: PipelineResult, top: int = 3) -> str:
    r = res.report
    lines = [
        "ROOT-CAUSE LOG ANALYZER",
        f"  sources={res.n_sources}  events={res.n_events:,}  workers={res.workers}",
        f"  load+scan={res.timings.load_scan:.3f}s  merge={res.timings.merge:.3f}s  "
        f"correlate={res.timings.correlate:.3f}s  total={res.timings.total:.3f}s",
        "",
    ]
    if r.failure is None:
        lines.append("No failure event (ERROR/FATAL) found.")
        return "\n".join(lines)
    lines.append(
        f"Failure : {_t(r.failure.ts)} [{r.failure.severity.name}] "
        f"{r.failure.service}: {r.failure.message}"
    )
    rc = r.root_cause
    if rc:
        lines.append(f"ROOT CAUSE (most likely): {rc.service}  (score {rc.score:.2f})")
    lines.append("Causal chain: " + " -> ".join(r.chain))
    lines.append("")
    for i, c in enumerate(r.candidates[:top], 1):
        sigs = ", ".join(c.signatures) or "-"
        lines.append(
            f"#{i} {c.service:<9} score={c.score:.2f}  first={_t(c.first_ts)}  "
            f"events={c.count}  max={c.max_severity.name}  signatures=[{sigs}]"
        )
        for e in c.evidence[:3]:
            lines.append(f"      {_t(e.ts)} [{e.severity.name}] {e.message}")
    if r.sequences:
        lines.append("")
        lines.append("Matched cascades:")
        for h in r.sequences:
            path = " -> ".join(f"{e.service}@{_t(e.ts)}" for e in h.events)
            lines.append(f"  * {h.pattern}: {path}")
    return "\n".join(lines)
