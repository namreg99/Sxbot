"""Labeled V2 wallets we already paired from unique fills.

Addresses vanish from the public API on V3 (August 25). The labels exist so
archive/profiles/mimic can talk about *styles* (Gary the soccer taker,
HedgeHog the two-sided MM) instead of hex strings. Pairing is not 100% for
BotswanaMC — it matched one unique Chargers/49ers fill.

`KNOWN_WALLETS` feed mimic / overlap / sharp. `CANDIDATE_WALLETS` are archive
research only — a 5-day heater does not become a copy target.
"""

from __future__ import annotations

from collections.abc import Sequence

from sxbot.config import Settings

# sportId from GET /sports
SPORT_FOOTBALL = 8
SPORT_BASKETBALL = 1
SPORT_BASEBALL = 3
SPORT_HOCKEY = 2
SPORT_SOCCER = 5
SPORT_TENNIS = 6

# Universe we actually care about: in-season US majors + soccer + tennis.
WATCH_SPORT_IDS = (
    SPORT_FOOTBALL,
    SPORT_BASKETBALL,
    SPORT_BASEBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
    SPORT_TENNIS,
)

KNOWN_WALLETS: dict[str, str] = {
    "GambleGuruGary": "0xfd3a22179ca4104e97c670b834750bd7ff310b17",
    "cypherprod": "0x8268fec8dcb0d284343054bdf2b649286c9aa0a1",
    "BotswanaMC": "0x97ec1c9682f70091efb04e97b0b34698cf815ee1",
    "HedgeHog": "0xe4dd6165bff7ee73d7932c3b478f7e22431e85e7",
}

# 5-day scan hits. Archive them on the same winter+summer windows as the four
# labeled wallets. Do not add them to KNOWN_WALLETS — that would turn mimic on.
CANDIDATE_WALLETS: dict[str, str] = {
    "TennisMix": "0x11e07140a8a41ab75521505a07f393b32996a06a",
    "SoccerTaker": "0x2ce91e5aeea0b936e18bc29d931fd235754f4a3a",
}

# BotswanaMC was paired from a single unique fill. Treat as medium confidence.
WALLET_NOTES: dict[str, str] = {
    "BotswanaMC": "medium-confidence pair (one unique Chargers/49ers fill)",
    "TennisMix": "research-only; winter+summer tennis −EV; 5-day heater is not the sample",
    "SoccerTaker": "research-only; no fills before 2026-07-23; $1k-stake month-old heater",
}


def _named_wallets() -> dict[str, str]:
    return {**KNOWN_WALLETS, **CANDIDATE_WALLETS}


def _label_for(address: str) -> str:
    lower = address.lower()
    for label, known in _named_wallets().items():
        if known == lower:
            return label
    return address[:6] + "…" + address[-4:]


def labeled_targets(settings: Settings | None = None) -> list[tuple[str, str]]:
    """(label, address) pairs. Env wallets are added on top of the known set."""
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    extra = settings.sharp_wallets if settings is not None else ()
    for address in extra:
        lower = address.lower()
        if lower in seen:
            continue
        seen.add(lower)
        ordered.append((_label_for(lower), lower))
    for label, address in KNOWN_WALLETS.items():
        if address in seen:
            continue
        seen.add(address)
        ordered.append((label, address))
    return ordered


def labeled_addresses(settings: Settings | None = None) -> dict[str, str]:
    """Lowercased address → display label (known set plus SX_SHARP_WALLETS)."""
    return {address: label for label, address in labeled_targets(settings)}


def archive_targets(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Known teachers plus research candidates. Mimic does not use this list."""
    ordered = labeled_targets(settings)
    seen = {address for _, address in ordered}
    for label, address in CANDIDATE_WALLETS.items():
        if address in seen:
            continue
        seen.add(address)
        ordered.append((label, address))
    return ordered


def resolve_archive_targets(
    names: Sequence[str],
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Filter `archive_targets` by label or address. Empty names → full list."""
    available = archive_targets(settings)
    if not names:
        return available
    by_label = {label.lower(): (label, address) for label, address in available}
    by_addr = {address: (label, address) for label, address in available}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in names:
        key = raw.strip().lower()
        hit = by_label.get(key) or by_addr.get(key)
        if hit is None and key.startswith("0x") and len(key) == 42:
            hit = (_label_for(key), key)
        if hit is None:
            known = ", ".join(label for label, _ in available)
            raise ValueError(f"unknown wallet {raw!r}; known labels: {known}")
        if hit[1] in seen:
            continue
        seen.add(hit[1])
        out.append(hit)
    return out
