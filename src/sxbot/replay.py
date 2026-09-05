"""Filter-backtest the unique tape under the live take_first model.

SX does not keep old order books, so this is not a rewind. It takes the
unique follows we already logged (imbalance, motive, side) and asks: would
live take_first still take this ticket?

Keep = MLB pick'em steam (size optional), soccer/dogs with maker size,
stale leftover, or a fade onto the in-band dog.
Skip = tennis shorts (even with size — live does not pay that spread),
and steam/tob_lag with no inventory except MLB pick'em.

The faded *other* side is not reconstructed — we only know the ticket we
wrote. Old thin shorts are counted as skips, not as hypothetical dog takes.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sxbot.board import is_maker_ev, paper_record
from sxbot.filters import STYLE_MLB, STYLE_TENNIS_SHORT

_FADE = "fade non-EV short"
_STEAM_MOTIVES = {"maker_steam", "size_rotation"}


def model_keep_reason(row: dict[str, Any], *, min_imbalance: float = 0.15) -> str | None:
    """Why live take_first would still take this unique, or None."""
    reason = str(row.get("reason") or "")
    if _FADE in reason:
        return "fade to size"
    motive = str(row.get("motive") or "")
    if motive == "take_stale":
        return "stale leftover"
    style = str(row.get("style") or "")
    if style == STYLE_TENNIS_SHORT:
        return None
    if style == STYLE_MLB and motive in _STEAM_MOTIVES:
        return "mlb steam"
    if motive == "tob_lag":
        return None
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
        "It splits the unique follow tape you already logged under the live",
        "take_first model: keep = MLB pick'em steam (size optional), soccer/",
        "dogs with maker size, stale leftover, fade-to-size. Skip = tennis",
        "shorts (even with size) and steam/tob_lag with no inventory except MLB.",
        "Faded dogs we never wrote are not reconstructed.",
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
