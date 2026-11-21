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
                    out.append("      -> high-churn verbatim reads; consider narrower slices or intent retrieval")
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
            out.append("    2. ref-verbatim: use targeted read slices or trimtok_retrieve intent/grep")
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
        revert_reasons: dict = {}
        shapes: dict = {}
        cmds: dict = {}
        for r in rows:
            if r.get("hook") in ("terminal", "tool"):
                g = str(r.get("gate") or "?")
                gates[g] = gates.get(g, 0) + 1
                if g in ("reverted_gate", "reverted_quality") and r.get("reason"):
                    rr = str(r.get("reason"))
                    revert_reasons[rr] = revert_reasons.get(rr, 0) + 1
                sh = str(r.get("shape") or "?")
                shapes[sh] = shapes.get(sh, 0) + 1
                c = str(r.get("cmd") or r.get("tool") or "?")[:40]
                cmds[c] = [cmds.get(c, [0, 0])[0] + 1, cmds.get(c, [0, 0])[1] + int(r.get("saved") or 0)]
        retr = [r for r in rows if r.get("hook") == "retrieve"]
        hits = sum(1 for r in retr if r.get("hit"))
        all_g = sorted(gates.items(), key=lambda kv: -kv[1])
        top_s = sorted(shapes.items(), key=lambda kv: -kv[1])[:5]
        top_c = sorted(cmds.items(), key=lambda kv: -kv[1][1])[:5]
        out = [f"trimtok usage: {len(rows)} events, {n_ok} folds, ~{saved:,} chars saved."]
        out.append("gates: " + ", ".join(f"{k}={v}" for k, v in all_g))
        if revert_reasons:
            rev_sorted = sorted(revert_reasons.items(), key=lambda kv: -kv[1])
            out.append("revert-reasons: " + ", ".join(f"{k}={v}" for k, v in rev_sorted))
        out.append("shapes: " + ", ".join(f"{k}={v}" for k, v in top_s))
        out.append("top-saved: " + "; ".join(f"{k} (~{v[1]:,}c)" for k, v in top_c))
        out.append(f"retrieve: {hits}/{len(retr)} hits" if retr else "retrieve: none yet")
        return "\n".join(out)
    except Exception:
        return "trimtok: usage unavailable."


def _safe_jsonl_scan(files: tuple[Path, ...]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    malformed = 0
    for f in files:
        if not f.exists():
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    s = raw.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                        if isinstance(obj, dict):
                            rows.append(obj)
                        else:
                            malformed += 1
                    except Exception:
                        malformed += 1
        except Exception:
            malformed += 1
    return rows, malformed


def run_doctor(
    data_dir: Path | None = None,
    scan_paths: list[str | Path] | None = None,
    *,
    max_entries: int = 1000,
    max_bytes: int = 50 * 1024 * 1024,
    ttl_hours: int = 24,
) -> dict[str, Any]:
    """Standalone diagnostics returning the unified Trinity doctor envelope."""
    import sqlite3

    try:
        from . import ccr
        from .core import VERSION
    except ImportError:
        import ccr  # type: ignore
        from core import VERSION  # type: ignore

    base = Path(data_dir) if data_dir is not None else ccr.default_dir()
    db_path = base / "trimtok_ccr_v2.sqlite3"
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")

    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "data_dir": str(base),
        "db_path": str(db_path),
    }

    if not db_path.exists():
        checks.append({
            "id": "ccr_sqlite_integrity",
            "status": "info",
            "detail": "CCR SQLite store has not been created yet.",
            "fix_hint": "Run any trimtok compress command or `trimtok install` to initialize.",
        })
        checks.append({
            "id": "ccr_storage",
            "status": "info",
            "detail": "0 bytes on disk.",
            "fix_hint": "",
        })
        checks.append({
            "id": "ccr_fts_sync",
            "status": "info",
            "detail": "FTS index not initialized yet.",
            "fix_hint": "",
        })
    else:
        db_bytes = db_path.stat().st_size
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        shm_bytes = shm_path.stat().st_size if shm_path.exists() else 0
        metrics.update({
            "db_disk_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "shm_bytes": shm_bytes,
        })

        conn = None
        try:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
                conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            except sqlite3.OperationalError:
                if conn is not None:
                    conn.close()
                conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=2.0)

            qc = conn.execute("PRAGMA quick_check").fetchone()
            qc_ok = bool(qc and qc[0] == "ok")
            checks.append({
                "id": "ccr_sqlite_integrity",
                "status": "ok" if qc_ok else "fail",
                "detail": f"PRAGMA quick_check={qc[0] if qc else 'unknown'}",
                "fix_hint": "" if qc_ok else "Run `trimtok purge` or recreate CCR store.",
            })

            chunks_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] or 0)
            pinned_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks WHERE pinned = 1").fetchone()[0] or 0)
            cutoff = time.time() - (ttl_hours * 3600.0) if ttl_hours > 0 else 0.0
            expired_cnt = (
                int(
                    conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE created_at < ? AND (pinned IS NULL OR pinned = 0)",
                        (cutoff,),
                    ).fetchone()[0]
                    or 0
                )
                if ttl_hours > 0
                else 0
            )
            metrics.update({
                "entries": chunks_cnt,
                "pinned_entries": pinned_cnt,
                "expired_entries": expired_cnt,
            })

            storage_issues: list[str] = []
            if (db_bytes + wal_bytes) > max_bytes:
                storage_issues.append(f"disk footprint {db_bytes + wal_bytes:,}B exceeds cap {max_bytes:,}B")
            if wal_bytes > 16 * 1024 * 1024:
                storage_issues.append(f"WAL size {wal_bytes:,}B is bloated (>16MB)")
            if chunks_cnt > max_entries:
                storage_issues.append(f"entry count {chunks_cnt} exceeds cap {max_entries}")
            checks.append({
                "id": "ccr_storage",
                "status": "warn" if storage_issues else "ok",
                "detail": (
                    "; ".join(storage_issues)
                    if storage_issues
                    else f"{chunks_cnt} chunks ({pinned_cnt} pinned, {expired_cnt} expired), db={db_bytes // 1024}KB, wal={wal_bytes // 1024}KB"
                ),
                "fix_hint": (
                    f"Run `trimtok purge --older-than {ttl_hours} --max-mb {max(1, max_bytes // (1024 * 1024))}`."
                    if storage_issues
                    else ""
                ),
            })

            has_fts = bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
                ).fetchone()
            )
            if not has_fts:
                checks.append({
                    "id": "ccr_fts_sync",
                    "status": "warn",
                    "detail": "chunks_fts virtual table missing.",
                    "fix_hint": "Re-open CCR store to backfill FTS5.",
                })
            else:
                fts_cnt = int(conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] or 0)
                metrics["fts_entries"] = fts_cnt
                in_sync = fts_cnt == chunks_cnt
                checks.append({
                    "id": "ccr_fts_sync",
                    "status": "ok" if in_sync else "warn",
                    "detail": f"chunks={chunks_cnt}, chunks_fts={fts_cnt}",
                    "fix_hint": "" if in_sync else "FTS5 index count diverges from chunks table.",
                })
        except Exception as exc:
            checks.append({
                "id": "ccr_sqlite_integrity",
                "status": "fail",
                "detail": f"Failed to inspect CCR SQLite DB: {exc}",
                "fix_hint": "Check file permissions or rebuild CCR store.",
            })
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    usage_path = base / "usage.jsonl"
    audit_path = base / "audit.jsonl"
    usage_rows, usage_bad = _safe_jsonl_scan((usage_path.with_name(usage_path.name + ".1"), usage_path))
    audit_rows, audit_bad = _safe_jsonl_scan((audit_path.with_name(audit_path.name + ".1"), audit_path))
    malformed_total = usage_bad + audit_bad

    logged_versions = [str(r["version"]) for r in usage_rows if r.get("version")]
    latest_logged_version = logged_versions[-1] if logged_versions else VERSION
    version_skew = bool(logged_versions and latest_logged_version != VERSION)

    gates: dict[str, int] = {}
    revert_reasons: dict[str, int] = {}
    for r in usage_rows:
        if r.get("hook") in ("terminal", "tool"):
            g = str(r.get("gate") or "unknown")
            gates[g] = gates.get(g, 0) + 1
            if g in ("reverted_gate", "reverted_quality"):
                rr = str(r.get("reason") or "unspecified")
                revert_reasons[rr] = revert_reasons.get(rr, 0) + 1

    fail_gates = {
        k: gates[k]
        for k in ("passthrough_timeout", "passthrough_storage_failure", "passthrough_exception")
        if gates.get(k, 0) > 0
    }
    metrics.update({
        "usage_events": len(usage_rows),
        "audit_events": len(audit_rows),
        "malformed_telemetry_lines": malformed_total,
        "latest_logged_version": latest_logged_version,
        "gates": gates,
        "reverted_reasons": revert_reasons,
        "failure_gates": fail_gates,
    })

    tel_warnings: list[str] = []
    if malformed_total > 0:
        tel_warnings.append(f"{malformed_total} malformed JSONL line(s)")
    if version_skew:
        tel_warnings.append(f"telemetry logged under v{latest_logged_version} vs running v{VERSION}")
    if fail_gates:
        tel_warnings.append("failure gates seen: " + ", ".join(f"{k}={v}" for k, v in fail_gates.items()))
    checks.append({
        "id": "telemetry_health",
        "status": "warn" if tel_warnings else "ok",
        "detail": (
            "; ".join(tel_warnings)
            if tel_warnings
            else f"{len(usage_rows)} usage + {len(audit_rows)} audit events parsed cleanly (v{VERSION})"
        ),
        "fix_hint": "Deploy updated plugin/package if version skew persists." if version_skew else "",
    })

    if scan_paths:
        try:
            from .guard import scan_paths as _guard_scan
        except ImportError:
            from guard import scan_paths as _guard_scan  # type: ignore
        scan_res = _guard_scan(scan_paths)
        metrics["stub_scan"] = scan_res
        checks.append({
            "id": "stub_scan",
            "status": "ok" if scan_res["clean"] else "fail",
            "detail": (
                f"Scanned {scan_res['files_scanned']} files, 0 persisted stubs found."
                if scan_res["clean"]
                else f"Found {len(scan_res['findings'])} persisted Trimtok stub(s) in {scan_res['corrupted_files']} file(s)."
            ),
            "fix_hint": "" if scan_res["clean"] else "Restore corrupted files from originals or git.",
        })

    statuses = {c["status"] for c in checks}
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "ok")
    return {
        "tool": "trimtok",
        "version": VERSION,
        "status": overall,
        "checks": checks,
        "metrics": metrics,
    }

