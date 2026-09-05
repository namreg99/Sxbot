"""Fit an inspectable maker scorer from sxbot-history.sqlite.

This is not a neural net. The public API does not keep old order books, so
there is nothing to replay. What the sqlite *does* have is settled maker
fills tagged by sport, market type, odds band, and pregame vs live. We store
empirical ROI in those cells and shrink toward 0% so a 20-fill hot streak
cannot override a $200k red bucket.

Maker rewards are a different payout: SX pays USDC while a qualifying limit
sits *unmatched* (mainline, typically ≥$100, better than the orange global
line, rest ≥10s). Those dollars are not in this DB, the orange line is not
on the REST book, and paper quotes never rest. Fill-ROI training and
rewards training pull in opposite directions — a fill stops reward points.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sxbot.archive import HistoryStore, ODDS_BUCKETS, odds_bucket
from sxbot.config import Settings
from sxbot.models import Market
from sxbot.units import decimal_odds, to_prob

MODEL_VERSION = 1
DEFAULT_PRIOR_STAKE = 25_000.0
DEFAULT_PRIOR_ROI = 0.0
MIN_CELL_N = 30
MIN_CELL_STAKE = 10_000.0

# Same kind labels as sxbot makers sport-band table.
_KIND = {
    1: "type-1",
    2: "totals",
    3: "spread",
    28: "totals",
    52: "ML",
    226: "ML",
    236: "totals",
    342: "spread",
}


def cell_kind(market_type: int) -> str:
    return _KIND.get(int(market_type or 0), f"type-{int(market_type or 0)}")


def cell_key(sport: str, kind: str, band: str, phase: str) -> str:
    return f"{sport}|{kind}|{band}|{phase}"


def key_for_quote(market: Market, price: int, *, now: int | None = None) -> str | None:
    if price <= 0:
        return None
    sport = (market.sport_label or "?").strip() or "?"
    kind = cell_kind(market.type)
    band = odds_bucket(decimal_odds(to_prob(price)))
    ts = int(now if now is not None else time.time())
    phase = "live" if market.is_live(ts) else "pregame"
    return cell_key(sport, kind, band, phase)


@dataclass(frozen=True)
class MakerCell:
    n: int
    stake: float
    pnl: float

    def raw_roi(self) -> float:
        return (self.pnl / self.stake) if self.stake else 0.0

    def shrunk_roi(self, prior_stake: float, prior_roi: float) -> float:
        denom = self.stake + prior_stake
        if denom <= 0:
            return prior_roi
        return (self.pnl + prior_roi * prior_stake) / denom


@dataclass
class MakerModel:
    """Lookup table: sport × kind × odds-band × phase → shrunk maker fill ROI."""

    cells: dict[str, MakerCell]
    prior_stake: float = DEFAULT_PRIOR_STAKE
    prior_roi: float = DEFAULT_PRIOR_ROI
    fitted_at: str = ""
    fills: int = 0
    version: int = MODEL_VERSION

    def roi(self, key: str) -> float | None:
        cell = self.cells.get(key)
        if cell is None:
            return None
        if cell.n < MIN_CELL_N or cell.stake < MIN_CELL_STAKE:
            return None
        return cell.shrunk_roi(self.prior_stake, self.prior_roi)

    def score(self, market: Market, price: int, *, now: int | None = None) -> float | None:
        key = key_for_quote(market, price, now=now)
        if key is None:
            return None
        return self.roi(key)

    def allow(self, market: Market, price: int, min_roi: float, *, now: int | None = None) -> float | None:
        """Shrunk ROI if the quote clears the floor, else None."""
        scored = self.score(market, price, now=now)
        if scored is None or scored < min_roi:
            return None
        return scored

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fitted_at": self.fitted_at,
            "fills": self.fills,
            "prior_stake": self.prior_stake,
            "prior_roi": self.prior_roi,
            "rewards_note": (
                "Fill ROI only. SX maker rewards accrue while unmatched (typically "
                "$100, beat orange global line, rest ≥10s, mainline) and are not in "
                "this sqlite. The orange line is not on the public book API."
            ),
            "odds_buckets": [[name, lo, hi] for name, lo, hi in ODDS_BUCKETS],
            "cells": {
                key: {"n": cell.n, "stake": round(cell.stake, 4), "pnl": round(cell.pnl, 4)}
                for key, cell in sorted(self.cells.items())
            },
        }

    def save(self, path: str | Path) -> None:
        file = Path(path)
        file.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> MakerModel:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cells = {
            key: MakerCell(n=int(row["n"]), stake=float(row["stake"]), pnl=float(row["pnl"]))
            for key, row in (raw.get("cells") or {}).items()
        }
        return cls(
            cells=cells,
            prior_stake=float(raw.get("prior_stake") or DEFAULT_PRIOR_STAKE),
            prior_roi=float(raw.get("prior_roi") or DEFAULT_PRIOR_ROI),
            fitted_at=str(raw.get("fitted_at") or ""),
            fills=int(raw.get("fills") or 0),
            version=int(raw.get("version") or MODEL_VERSION),
        )


def fit_maker_model(
    store: HistoryStore,
    *,
    prior_stake: float = DEFAULT_PRIOR_STAKE,
    prior_roi: float = DEFAULT_PRIOR_ROI,
) -> MakerModel:
    """Pregame+live maker fills → shrunk ROI cells. No book features exist to fit."""
    acc: dict[str, list[float]] = {}
    n_fills = 0
    rows = store._db.execute(
        """
        SELECT f.stake, f.odds, f.bet_time, f.pnl_usdc,
               m.sport_label, m.type, m.game_time
        FROM fills f
        LEFT JOIN markets m ON m.market_hash = f.market_hash
        WHERE f.settled=1 AND f.is_maker=1
        """
    )
    for r in rows:
        n_fills += 1
        try:
            odds = int(r["odds"] or 0)
        except (TypeError, ValueError):
            continue
        if odds <= 0:
            continue
        dec = decimal_odds(to_prob(odds))
        sport = str(r["sport_label"] or "?").strip() or "?"
        kind = cell_kind(int(r["type"] or 0))
        gt = int(r["game_time"] or 0)
        bt = int(r["bet_time"] or 0)
        phase = "live" if gt and bt >= gt else "pregame"
        key = cell_key(sport, kind, odds_bucket(dec), phase)
        bucket = acc.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += int(r["stake"] or 0) / 1e6
        bucket[2] += float(r["pnl_usdc"] or 0)
    cells = {
        key: MakerCell(n=int(n), stake=stake, pnl=pnl) for key, (n, stake, pnl) in acc.items()
    }
    fitted = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return MakerModel(
        cells=cells,
        prior_stake=float(prior_stake),
        prior_roi=float(prior_roi),
        fitted_at=fitted,
        fills=n_fills,
    )


def load_or_fit_maker_model(settings: Settings) -> MakerModel | None:
    """Load sxbot-maker-model.json, or fit from sqlite if the file is missing."""
    path = Path(settings.mm_model_path)
    if path.exists():
        return MakerModel.load(path)
    sqlite = Path(settings.archive_path)
    if not sqlite.exists():
        return None
    with HistoryStore(sqlite) as store:
        model = fit_maker_model(store, prior_stake=settings.mm_fit_prior_stake)
    model.save(path)
    return model


def format_maker_model(model: MakerModel, *, limit: int = 16) -> str:
    lines = [
        "Maker fill-ROI model (empirical Bayes, shrink toward 0%).",
        "Not a neural net — sqlite has fills, not historical books.",
        f"fitted {model.fitted_at or 'now'}  fills {model.fills}  "
        f"prior ${model.prior_stake:,.0f} @ {model.prior_roi*100:.1f}%  cells {len(model.cells)}",
        "",
        f"{'cell':<48} {'n':>5} {'stake':>10} {'raw%':>7} {'shrunk%':>8}",
    ]
    ranked: list[tuple[float, str]] = []
    for key, cell in model.cells.items():
        if cell.n < MIN_CELL_N or cell.stake < MIN_CELL_STAKE:
            continue
        shrunk = cell.shrunk_roi(model.prior_stake, model.prior_roi)
        ranked.append(
            (
                shrunk,
                f"{key:<48} {cell.n:5d} {cell.stake:10,.0f} {cell.raw_roi()*100:7.1f} {shrunk*100:8.1f}",
            )
        )
    ranked.sort(reverse=True)
    if not ranked:
        lines.append("  (no cells with enough sample)")
    else:
        lines.extend(row for _, row in ranked[:limit])
        lines.append("")
        green = sum(1 for roi, _ in ranked if roi > 0)
        lines.append(f"{green} green / {len(ranked)} sizable cells after shrink.")
    lines.append("")
    lines.append(
        "Maker rewards are NOT in these numbers. They pay while a live limit stays "
        "unmatched (beat orange line, typically $100, ≥10s, mainline). Paper cannot "
        "collect them. Getting filled stops reward points."
    )
    return "\n".join(lines)
