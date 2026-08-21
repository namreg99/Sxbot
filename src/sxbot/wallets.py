"""Labeled V2 wallets we already paired from unique fills.

Addresses vanish from the public API on V3 (August 25). The labels exist so
archive/profiles/mimic can talk about *styles* (Gary the soccer taker,
HedgeHog the two-sided MM) instead of hex strings. Pairing is not 100% for
BotswanaMC — it matched one unique Chargers/49ers fill.
"""

from __future__ import annotations

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

# BotswanaMC was paired from a single unique fill. Treat as medium confidence.
WALLET_NOTES: dict[str, str] = {
    "BotswanaMC": "medium-confidence pair (one unique Chargers/49ers fill)",
}


def _label_for(address: str) -> str:
    lower = address.lower()
    for label, known in KNOWN_WALLETS.items():
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
