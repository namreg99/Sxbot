"""Fingerprint V2 sharp wallets so their *habits* survive V3 anonymity.

Addresses die on August 25. What can transfer is: are they mostly makers or
takers, which sports, pregame vs live, and whether our book classifier
lights up on the same markets they are quoting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sxbot.v2 import remaining_size


def role_from_counts(maker_fills: int, taker_fills: int) -> str:
    total = maker_fills + taker_fills
    if total <= 0:
        return "unknown"
    share = maker_fills / total
    if share >= 0.7:
        return "maker"
    if share <= 0.3:
        return "taker"
    return "mixed"


def suggested_style(role: str) -> str:
    return {"maker": "join", "taker": "take", "mixed": "mixed"}.get(role, "join")


def trade_pnl_usdc(raw: dict[str, Any]) -> float | None:
    if not raw.get("settled"):
        return None
    outcome = raw.get("outcome")
    if outcome is not None and int(outcome) == 0:
        return 0.0
    stake = int(raw.get("stake") or 0) / 1e6
    returned = float(raw.get("settleNetReturnValue") or 0)
    return returned - stake


def summarize_fills(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [trade_pnl_usdc(row) for row in rows]
    settled = [p for p in pnls if p is not None]
    wins = sum(1 for p in settled if p > 0)
    losses = sum(1 for p in settled if p < 0)
    voids = sum(1 for p in settled if p == 0)
    return {
        "fills": len(rows),
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "pnl_usdc": round(sum(settled), 2) if settled else 0.0,
        "win_rate": (wins / (wins + losses)) if (wins + losses) else None,
    }


def open_quote_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for order in orders:
        size = remaining_size(order)
        if size <= 0:
            continue
        out.append(
            {
                "market": order.get("marketHash"),
                "side": "outcome_one" if order.get("isMakerBettingOutcomeOne") else "outcome_two",
                "odds": str(order.get("percentageOdds") or "0"),
                "size": size,
                "size_usdc": size / 1e6,
            }
        )
    return out


@dataclass(frozen=True)
class WalletProfile:
    address: str
    role: str
    style: str
    maker: dict[str, Any]
    taker: dict[str, Any]
    open_quotes: int
    two_sided_markets: int
    sports: tuple[str, ...]


def profile_wallet(
    address: str,
    *,
    maker_fills: list[dict[str, Any]],
    taker_fills: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    markets: dict[str, dict[str, Any]] | None = None,
) -> WalletProfile:
    maker = summarize_fills(maker_fills)
    taker = summarize_fills(taker_fills)
    role = role_from_counts(maker["fills"], taker["fills"])
    quotes = open_quote_rows(open_orders)
    by_market: dict[str, set[str]] = {}
    for quote in quotes:
        by_market.setdefault(str(quote["market"]), set()).add(str(quote["side"]))
    two_sided = sum(1 for sides in by_market.values() if len(sides) >= 2)
    sports: Counter[str] = Counter()
    for market in (markets or {}).values():
        label = str(market.get("sportLabel") or market.get("leagueLabel") or "")
        if label:
            sports[label] += 1
    return WalletProfile(
        address=address,
        role=role,
        style=suggested_style(role),
        maker=maker,
        taker=taker,
        open_quotes=len(quotes),
        two_sided_markets=two_sided,
        sports=tuple(name for name, _ in sports.most_common(6)),
    )


def format_profiles(profiles: list[WalletProfile]) -> str:
    if not profiles:
        return (
            "No wallets configured. Put the three addresses in SX_SHARP_WALLETS "
            "(comma-separated) and run `sxbot sharp` again. After August 25 the "
            "addresses go dark; this command exists to learn how they trade "
            "while V2 still shows them."
        )
    lines = [
        "These addresses will not be visible on V3. What transfers is the habit:",
        "maker vs taker, sports, two-sided quotes, and whether our book signals",
        "light up on the same markets. Suggested SX_FOLLOW_STYLE is per wallet.",
        "",
    ]
    styles = Counter(p.style for p in profiles)
    if len(styles) == 1:
        only = next(iter(styles))
        lines.append(f"Suggested bot style from this set: SX_FOLLOW_STYLE={only}")
    else:
        lines.append(
            "These wallets do not agree on style. Mixed is the honest default "
            f"(votes: {dict(styles)})."
        )
        lines.append("Suggested bot style: SX_FOLLOW_STYLE=mixed")
    lines.append("")
    for profile in profiles:
        short = profile.address[:6] + "…" + profile.address[-4:]
        lines.append(f"{short}  role={profile.role}  follow={profile.style}")
        lines.append(
            f"  maker fills {profile.maker['fills']}  "
            f"win {profile.maker['wins']}/{profile.maker['losses']}  "
            f"pnl {profile.maker['pnl_usdc']:+.1f} USDC"
        )
        lines.append(
            f"  taker fills {profile.taker['fills']}  "
            f"win {profile.taker['wins']}/{profile.taker['losses']}  "
            f"pnl {profile.taker['pnl_usdc']:+.1f} USDC"
        )
        lines.append(
            f"  open quotes {profile.open_quotes}  two-sided markets {profile.two_sided_markets}"
        )
        if profile.sports:
            lines.append("  sports  " + ", ".join(profile.sports))
        lines.append("")
    return "\n".join(lines).rstrip()
