"""Telemetry reporting: usage summaries over the JSONL log.

Every function takes the usage-log path explicitly instead of resolving it from a
module global. The plugin's tests patch `trimtok._usage_path` to redirect the log
into a temp dir; keeping the lookup in the caller is what keeps those patches
effective, and it makes each summary a pure function of a file.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Iterator

_LOGGER = logging.getLogger("trimtok.report")


def iter_usage_rows(path: Path, lock: Any = None) -> Iterator[dict]:
    """Stream telemetry rows from backup and current usage files. Never raises.

    A generator on purpose: the caller's lock must stay held for the whole scan,
    otherwise a concurrent writer can rotate the file mid-iteration.
    """
    try:
        backup = path.with_name(path.name + ".1")
        if lock is None:
            yield from _rows_from((backup, path))
            return
        with lock:
            yield from _rows_from((backup, path))
    except Exception as exc:
        _LOGGER.warning("trimtok usage discovery failed: %s", exc)
        return


def _rows_from(files: tuple[Path, ...]) -> Iterator[dict]:
    for f in files:
        if not f.exists():
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            yield json.loads(line)
                        except Exception as exc:
                            _LOGGER.warning("trimtok usage row parse failed in %s: %s", f, exc)
        except Exception as exc:
            _LOGGER.warning("trimtok usage file read failed for %s: %s", f, exc)


def discover_summary(path: Path, lock: Any = None) -> str:
    """Scan telemetry to find shapes/tools with low savings and suggest filters."""
    try:
        terminal_rows = [r for r in iter_usage_rows(path, lock) if r.get("hook") in ("terminal", "tool")]
        if not terminal_rows:
            return "trimtok: no terminal/tool events logged yet."

        # By shape
        by_shape: dict = {}
        for r in terminal_rows:
            sh = str(r.get("shape") or "?")
            if sh not in by_shape:
                by_shape[sh] = {"count": 0, "before": 0, "saved": 0}
            by_shape[sh]["count"] += 1
            by_shape[sh]["before"] += int(r.get("before") or 0)
            by_shape[sh]["saved"] += int(r.get("saved") or 0)

        # By tool/cmd
        by_cmd: dict = {}
        for r in terminal_rows:
            c = str(r.get("cmd") or r.get("tool") or "?")[:50]
            if c not in by_cmd:
                by_cmd[c] = {"count": 0, "before": 0, "saved": 0}
            by_cmd[c]["count"] += 1
            by_cmd[c]["before"] += int(r.get("before") or 0)
            by_cmd[c]["saved"] += int(r.get("saved") or 0)

        out = [f"trimtok discover: {len(terminal_rows)} events analyzed"]
        out.append("")

        # Low-savings shapes
        low_shapes = [(sh, d) for sh, d in by_shape.items()
                      if d["before"] > 0 and (d["saved"] / d["before"]) < 0.3 and d["count"] >= 2]
        if low_shapes:
            out.append("  shapes with <30% savings (need filters):")
            for sh, d in sorted(low_shapes, key=lambda kv: kv[1]["count"], reverse=True):
                pct = (d["saved"] / d["before"] * 100) if d["before"] > 0 else 0.0
                out.append(f"    {sh}: {d['count']} events, {pct:.1f}% saved ({d['before']:,} chars)")
                if sh == "json":
                    out.append("      -> merged TOON already helps; consider command-specific JSON filters")
                elif sh == "ref-verbatim":
                    out.append("      -> AST code skeletonizer needed")
                elif sh == "mixed":
                    out.append("      -> command-specific filters needed")
            out.append("")

        # High-volume low-savings commands
        low_cmds = [(c, d) for c, d in by_cmd.items()
                    if d["before"] > 10000 and (d["saved"] / d["before"]) < 0.2 and d["count"] >= 2]
        if low_cmds:
            out.append("  high-volume commands with <20% savings:")
            for c, d in sorted(low_cmds, key=lambda kv: kv[1]["before"], reverse=True)[:10]:
                pct = (d["saved"] / d["before"] * 100) if d["before"] > 0 else 0.0
                out.append(f"    {c}: {d['count']}x, {pct:.1f}% saved ({d['before']:,} chars)")
            out.append("")

        # Summary recommendations
        out.append("  recommendations:")
        if any(sh == "json" for sh, _ in low_shapes):
            out.append("    1. JSON shape: merged TOON active; next: command-specific JSON filters")
        if any(sh == "ref-verbatim" for sh, _ in low_shapes):
            out.append("    2. ref-verbatim: implement AST code skeletonizer")
        if any(sh == "mixed" for sh, _ in low_shapes):
            out.append("    3. mixed shape: implement command-specific filters")
        out.append("    4. Run 'trimtok gain' for detailed breakdown")
        return "\n".join(out)
    except Exception as e:
        return f"trimtok: discover unavailable ({e})."


def gain_summary(path: Path, days: int = 0, lock: Any = None) -> str:
    """Detailed gain report: savings by shape, tool, and bottleneck analysis."""
    try:
        cutoff = (time.time() - (days * 86400)) if days > 0 else 0.0
        rows = []
        for r in iter_usage_rows(path, lock):
            if cutoff and float(r.get("ts") or 0.0) < cutoff:
                continue
            rows.append(r)
        if not rows:
            return "trimtok gain: no usage logged yet."

        # Overall stats
        terminal_rows = [r for r in rows if r.get("hook") in ("terminal", "tool")]
        n_folds = sum(1 for r in terminal_rows if r.get("gate") == "ok")
        total_before = sum(int(r.get("before") or 0) for r in terminal_rows)
        total_after = sum(int(r.get("after") or 0) for r in terminal_rows)
        total_saved = sum(int(r.get("saved") or 0) for r in terminal_rows)
        overall_pct = (total_saved / total_before * 100) if total_before > 0 else 0.0
        avg_ms = sum(float(r.get("ms") or 0) for r in terminal_rows) / max(len(terminal_rows), 1)
        retrieve_rows = [r for r in rows if r.get("hook") == "retrieve"]
        hits = sum(1 for r in retrieve_rows if r.get("hit"))
        exec_rows = [r for r in rows if r.get("hook") == "exec"]
        exec_ok = sum(1 for r in exec_rows if r.get("ok"))
        exec_queried = sum(int(r.get("payload_bytes") or 0) for r in exec_rows if r.get("ok"))

        # Savings by shape
        by_shape: dict = {}
        for r in terminal_rows:
            sh = str(r.get("shape") or "?")
            if sh not in by_shape:
                by_shape[sh] = {"count": 0, "before": 0, "after": 0, "saved": 0, "ms": 0.0, "ok": 0}
            by_shape[sh]["count"] += 1
            by_shape[sh]["before"] += int(r.get("before") or 0)
            by_shape[sh]["after"] += int(r.get("after") or 0)
            by_shape[sh]["saved"] += int(r.get("saved") or 0)
            by_shape[sh]["ms"] += float(r.get("ms") or 0)
            if r.get("gate") == "ok":
                by_shape[sh]["ok"] += 1

        # Savings by tool/cmd
        by_cmd: dict = {}
        for r in terminal_rows:
            c = str(r.get("cmd") or r.get("tool") or "?")[:40]
            if c not in by_cmd:
                by_cmd[c] = {"count": 0, "before": 0, "after": 0, "saved": 0, "ms": 0.0}
            by_cmd[c]["count"] += 1
            by_cmd[c]["before"] += int(r.get("before") or 0)
            by_cmd[c]["after"] += int(r.get("after") or 0)
            by_cmd[c]["saved"] += int(r.get("saved") or 0)
            by_cmd[c]["ms"] += float(r.get("ms") or 0)

        out = []
        out.append(f"trimtok gain report ({len(rows)} events, {n_folds} folds)")
        if days > 0:
            out.append(f"  period: last {days} days")
        out.append(f"  total: {total_before:,} -> {total_after:,} chars ({overall_pct:.1f}% saved, {total_saved:,} chars)")
        out.append(f"  avg latency: {avg_ms:.1f}ms per transform")
        out.append(f"  retrieve: {hits}/{len(retrieve_rows)} hits")
        if exec_rows:
            out.append(f"  exec: {exec_ok}/{len(exec_rows)} ok ({exec_queried:,} payload bytes queried)")

        # Shape breakdown
        out.append("")
        out.append("  savings by shape:")
        out.append(f"  {'shape':<16} {'count':>6} {'before':>10} {'saved':>10} {'%':>7} {'avg_ms':>8}")
        out.append("  " + "-" * 62)
        for sh, d in sorted(by_shape.items(), key=lambda kv: -kv[1]["saved"]):
            pct = (d["saved"] / d["before"] * 100) if d["before"] > 0 else 0.0
            avg = d["ms"] / max(d["count"], 1)
            out.append(f"  {sh:<16} {d['count']:>6} {d['before']:>10,} {d['saved']:>10,} {pct:>6.1f}% {avg:>7.1f}")

        # Tool/cmd breakdown (top 10)
        out.append("")
        out.append("  savings by tool/cmd (top 10):")
        out.append(f"  {'cmd/tool':<40} {'count':>6} {'saved':>10} {'%':>7}")
        out.append("  " + "-" * 68)
        for c, d in sorted(by_cmd.items(), key=lambda kv: -kv[1]["saved"])[:10]:
            pct = (d["saved"] / d["before"] * 100) if d["before"] > 0 else 0.0
            out.append(f"  {c:<40} {d['count']:>6} {d['saved']:>10,} {pct:>6.1f}%")

        # Bottleneck analysis
        low_savers = [(sh, d) for sh, d in by_shape.items()
                      if d["before"] > 0 and (d["saved"] / d["before"]) < 0.1 and d["count"] >= 3]
        if low_savers:
            out.append("")
            out.append("  bottlenecks (shapes with <10% savings, 3+ events):")
            for sh, d in sorted(low_savers, key=lambda kv: kv[1]["count"], reverse=True):
                pct = (d["saved"] / d["before"] * 100) if d["before"] > 0 else 0.0
                out.append(f"    {sh}: {d['count']} events, {pct:.1f}% saved ({d['before']:,} chars)")
            out.append("  -> consider adding command-specific filters or TOON improvements for these shapes")

        # Gate breakdown
        gates: dict = {}
        for r in terminal_rows:
            g = str(r.get("gate") or "?")
            gates[g] = gates.get(g, 0) + 1
        out.append("")
        out.append("  gates: " + ", ".join(f"{k}={v}" for k, v in sorted(gates.items(), key=lambda kv: -kv[1])))

        return "\n".join(out)
    except Exception as e:
        return f"trimtok: gain unavailable ({e})."


def usage_summary(path: Path, lock: Any = None) -> str:
    try:
        rows = list(iter_usage_rows(path, lock))
        if not rows:
            return "trimtok: no usage logged yet."
        n_ok = sum(1 for r in rows if r.get("gate") == "ok")
        saved = sum(int(r.get("saved") or 0) for r in rows if r.get("hook") in ("terminal", "tool"))
        gates: dict = {}
        shapes: dict = {}
        cmds: dict = {}
        for r in rows:
            if r.get("hook") in ("terminal", "tool"):
                g = str(r.get("gate") or "?")
                gates[g] = gates.get(g, 0) + 1
                sh = str(r.get("shape") or "?")
                shapes[sh] = shapes.get(sh, 0) + 1
                c = str(r.get("cmd") or r.get("tool") or "?")[:40]
                cmds[c] = [cmds.get(c, [0, 0])[0] + 1, cmds.get(c, [0, 0])[1] + int(r.get("saved") or 0)]
        retr = [r for r in rows if r.get("hook") == "retrieve"]
        hits = sum(1 for r in retr if r.get("hit"))
        top_g = sorted(gates.items(), key=lambda kv: -kv[1])[:5]
        top_s = sorted(shapes.items(), key=lambda kv: -kv[1])[:5]
        top_c = sorted(cmds.items(), key=lambda kv: -kv[1][1])[:5]
        out = [f"trimtok usage: {len(rows)} events, {n_ok} folds, ~{saved:,} chars saved."]
        out.append("gates: " + ", ".join(f"{k}={v}" for k, v in top_g))
        out.append("shapes: " + ", ".join(f"{k}={v}" for k, v in top_s))
        out.append("top-saved: " + "; ".join(f"{k} (~{v[1]:,}c)" for k, v in top_c))
        out.append(f"retrieve: {hits}/{len(retr)} hits" if retr else "retrieve: none yet")
        return "\n".join(out)
    except Exception:
        return "trimtok: usage unavailable."

