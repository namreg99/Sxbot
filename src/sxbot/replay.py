"""Filter-backtest the unique tape under the size-not-steam model.

SX does not keep old order books, so this is not a rewind. It takes the
unique follows we already logged (imbalance, motive, side) and asks: would
the current model still take this ticket? Keep = maker size on our side,
stale leftover, or a fade onto the in-band dog. Skip = steam/tob_lag with
no inventory.

That is the same split as the board EV vs not-EV cards, plus fades.
The faded *other* side is not reconstructed — we only know the ticket we
wrote. Old thin shorts are counted as skips, not as hypothetical dog takes.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sxbot.board import is_maker_ev, paper_record

_FADE = "fade non-EV short"


def model_keep_reason(row: dict[str, Any], *, min_imbalance: float = 0.15) -> str | None:
    """Why the size-not-steam model would still take this unique, or None."""
    reason = str(row.get("reason") or "")
    if _FADE in reason:
        return "fade to size"
    motive = str(row.get("motive") or "")
    if motive == "take_stale":
        return "stale leftover"
    if is_maker_ev(row, min_imbalance=min_imbalance):
        return "maker size"
    return None


def split_model(
    views: list[dict[str, Any]],
    *,
    min_imbalance: float = 0.15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    keep: list[dict[str, Any]] = []
    skip: list[dict[str, Any]] = []
    for view in views:
        if model_keep_reason(view, min_imbalance=min_imbalance):
            keep.append(view)
        else:
            skip.append(view)
    return keep, skip


def _line(label: str, rec: dict[str, Any]) -> str:
    n = int(rec.get("n") or 0)
    wins = int(rec.get("wins") or 0)
    losses = int(rec.get("losses") or 0)
    wp = rec.get("win_pct")
    roi = rec.get("roi_pct")
    wp_s = f"{wp:.0f}%" if wp is not None else "—"
    roi_s = f"ROI {roi:+.0f}%" if roi is not None else "ROI —"
    pending = int(rec.get("pending") or 0)
    return (
        f"{label:<16} {n:>4} unique  {wins}–{losses}  {wp_s:<4}  {roi_s}"
        f"  pending {pending}"
    )


def format_replay(
    all_views: list[dict[str, Any]],
    keep: list[dict[str, Any]],
    skip: list[dict[str, Any]],
) -> str:
    lines = [
        "This is NOT a rewind of old SX books (they do not exist).",
        "It splits the unique follow tape you already logged: keep = maker size",
        "on our side / stale leftover / fade-to-size. Skip = steam or tob_lag",
        "with no inventory. Faded dogs we never wrote are not reconstructed.",
        "",
        _line("all unique", paper_record(all_views)),
        _line("keep model", paper_record(keep)),
        _line("skip", paper_record(skip)),
    ]
    by_style: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for view in keep:
        by_style[str(view.get("style") or "legacy")].append(view)
    if by_style:
        lines.append("")
        lines.append("keep by style")
        for style in sorted(by_style):
            lines.append(_line(f"  {style}", paper_record(by_style[style])))
    skip_styles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for view in skip:
        skip_styles[str(view.get("style") or "legacy")].append(view)
    if skip_styles:
        lines.append("")
        lines.append("skip by style")
        for style in sorted(skip_styles):
            lines.append(_line(f"  {style}", paper_record(skip_styles[style])))
    settled = int(paper_record(keep).get("wins") or 0) + int(paper_record(keep).get("losses") or 0)
    if settled < 30:
        lines.append("")
        lines.append(
            f"Only {settled} settled keep tickets. Treat this as a screen, not proof."
        )
    return "\n".join(lines)
