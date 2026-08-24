"""Read the flow and paper logs and print where informed size showed up."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from sxbot.filters import QUOTE_STYLES


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    file = Path(path)
    if not file.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def paper_log_for(path: str | Path, style: str) -> Path:
    file = Path(path)
    if not style:
        return file
    return file.with_name(f"{file.stem}-{style}{file.suffix}")


def iter_paper_logs(path: str | Path) -> list[Path]:
    """Main paper log plus per-style files that already exist."""
    file = Path(path)
    out: list[Path] = []
    seen: set[Path] = set()
    for candidate in (file, *(paper_log_for(file, style) for style in QUOTE_STYLES)):
        resolved = candidate if not candidate.exists() else candidate.resolve()
        key = resolved
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists():
            out.append(candidate)
    return out


def load_all_paper(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in iter_paper_logs(path):
        rows.extend(load_jsonl(log))
    rows.sort(key=lambda row: float(row.get("ts") or 0))
    return rows


def _count(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(row.get(key) or "-") for row in rows)


def format_summary(flow_path: str | Path, paper_path: str | Path) -> str:
    flow = load_jsonl(flow_path)
    paper_files = iter_paper_logs(paper_path)
    paper = load_all_paper(paper_path)
    lines: list[str] = []
    lines.append(f"flow events   {len(flow)}   ({flow_path})")
    if paper_files:
        for log in paper_files:
            n = len(load_jsonl(log))
            lines.append(f"paper orders  {n}   ({log})")
    else:
        lines.append(f"paper orders  0   ({paper_path})")
    if not flow and not paper:
        lines.append(
            "No logs yet. Leave `sxbot run` going — it only writes while the process is up."
        )
        return "\n".join(lines)

    if flow:
        lines.append("")
        lines.append("sharp-money motives (book flow, no wallets)")
        for motive, n in _count(flow, "motive").most_common():
            lines.append(f"  {motive:<16} {n:>5}")
        lines.append("phase")
        for phase, n in _count(flow, "phase").most_common():
            lines.append(f"  {phase:<16} {n:>5}")
        actionable = sum(1 for row in flow if row.get("actionable"))
        lines.append(f"actionable      {actionable:>5} / {len(flow)}")
        lines.append("")
        lines.append("last flow")
        for row in flow[-8:]:
            side = row.get("side") or "-"
            lines.append(
                f"  {row.get('phase') or '-':<8} {row.get('motive') or '-':<14} "
                f"{side:<12} {(row.get('league') or '')[:12]:<12} "
                f"{(row.get('label') or '')[:36]}"
            )

    if paper:
        lines.append("")
        lines.append("paper orders (dry-run, not submitted)")
        for action, n in _count(paper, "action").most_common():
            lines.append(f"  {action:<16} {n:>5}")
        stake = sum(float(row.get("stake_usdc") or 0) for row in paper)
        lines.append(f"  intended stake  {stake:.1f} USDC")
        lines.append("last paper")
        for row in paper[-8:]:
            lines.append(
                f"  {row.get('phase') or '-':<8} {row.get('action') or '-':<12} "
                f"{(row.get('side') or '-'):<12} {(row.get('label') or '')[:36]}  "
                f"{row.get('odds_pct')}%"
            )
    return "\n".join(lines)


def print_summary(flow_path: str | Path, paper_path: str | Path) -> None:
    print(format_summary(flow_path, paper_path))
