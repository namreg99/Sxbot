from datetime import datetime, timezone

from sxbot.archive import (
    HistoryStore,
    fill_row,
    format_style_profiles,
    load_profiles,
    odds_bucket,
    parse_day,
    profile_from_rows,
)
from sxbot.units import from_percent
from sxbot.wallets import KNOWN_WALLETS, WATCH_SPORT_IDS, labeled_targets


def test_tennis_is_in_the_watch_universe() -> None:
    assert 6 in WATCH_SPORT_IDS
    assert 5 in WATCH_SPORT_IDS
    assert 8 in WATCH_SPORT_IDS


def test_known_wallets_include_gary_and_hedgehog() -> None:
    labels = set(KNOWN_WALLETS)
    assert "GambleGuruGary" in labels
    assert "HedgeHog" in labels
    assert "cypherprod" in labels
    assert "BotswanaMC" in labels
    names = [label for label, _ in labeled_targets()]
    assert names[0] in labels


def test_odds_buckets() -> None:
    assert odds_bucket(1.10) == "hammer ≤1.12"
    assert odds_bucket(1.25) == "short 1.12–1.40"
    assert odds_bucket(1.97) == "pick 1.80–2.20"
    assert odds_bucket(7.84) == "longshot >3.50"


def test_parse_day_is_utc() -> None:
    assert parse_day("2025-12-01") == int(
        datetime(2025, 12, 1, tzinfo=timezone.utc).timestamp()
    )


def _fill(
    *,
    fill: str,
    wallet: str,
    maker: bool,
    market: str,
    event: str,
    one: bool,
    stake: str,
    pct: float,
    bet_time: int,
    settled: bool = True,
    outcome: int = 1,
    net: float = 10.0,
) -> dict:
    raw = {
        "fillHash": fill,
        "marketHash": market,
        "sportXeventId": event,
        "bettingOutcomeOne": one,
        "stake": stake,
        "odds": str(from_percent(pct)),
        "betTime": bet_time,
        "settled": settled,
        "outcome": outcome,
        "settleNetReturnValue": net,
        "maker": maker,
    }
    row = fill_row(raw, wallet=wallet, is_maker=maker)
    assert row is not None
    return row


def test_style_profile_from_sqlite(tmp_path) -> None:
    path = tmp_path / "hist.sqlite"
    addr = KNOWN_WALLETS["GambleGuruGary"]
    kickoff = 1_800_000_000
    with HistoryStore(path) as store:
        store.upsert_wallet(addr, "GambleGuruGary")
        store.upsert_fills(
            [
                _fill(
                    fill="0xa",
                    wallet=addr,
                    maker=False,
                    market="0xm1",
                    event="E1",
                    one=True,
                    stake="10000000",
                    pct=88.5,  # ~1.13 decimal
                    bet_time=kickoff - 3600,
                    net=11.3,
                ),
                _fill(
                    fill="0xb",
                    wallet=addr,
                    maker=False,
                    market="0xm1",
                    event="E1",
                    one=True,
                    stake="20000000",
                    pct=77.5,
                    bet_time=kickoff - 1800,
                    net=0.0,
                    outcome=2,
                ),
                _fill(
                    fill="0xc",
                    wallet=addr,
                    maker=False,
                    market="0xm2",
                    event="E2",
                    one=True,
                    stake="5000000",
                    pct=55.0,
                    bet_time=kickoff + 60,
                    net=9.09,
                ),
            ]
        )
        store.upsert_markets(
            [
                {
                    "market_hash": "0xm1",
                    "sport_id": 5,
                    "sport_label": "Soccer",
                    "league_id": 1,
                    "league_label": "MLS",
                    "event_id": "E1",
                    "team_one": "A",
                    "team_two": "B",
                    "outcome_one": "A",
                    "outcome_two": "B",
                    "game_time": kickoff,
                    "type": 226,
                    "line": None,
                    "main_line": 1,
                    "status": "INACTIVE",
                    "reported_outcome": 1,
                },
                {
                    "market_hash": "0xm2",
                    "sport_id": 6,
                    "sport_label": "Tennis",
                    "league_id": 2,
                    "league_label": "ATP",
                    "event_id": "E2",
                    "team_one": "T",
                    "team_two": "S",
                    "outcome_one": "T",
                    "outcome_two": "S",
                    "game_time": kickoff,
                    "type": 226,
                    "line": None,
                    "main_line": 1,
                    "status": "INACTIVE",
                    "reported_outcome": 1,
                },
            ]
        )
        profiles = load_profiles(store)
        assert store.fills_in_window(addr, as_maker=False, start=kickoff - 10_000, end=kickoff) == 2
    assert len(profiles) == 1
    p = profiles[0]
    assert p.label == "GambleGuruGary"
    assert p.role == "taker"
    assert p.style == "take"
    assert p.fills == 3
    assert p.tennis_fills == 1
    assert p.soccer_fills == 2
    assert p.live_share is not None
    assert p.live_share < 0.5  # two pregame, one live
    text = format_style_profiles(profiles)
    assert "GambleGuruGary" in text
    assert "Tennis" in text or "tennis" in text
    assert "SX_FOLLOW_STYLE=take" in text


def test_scale_in_and_two_sided(tmp_path) -> None:
    addr = "0x" + "ab" * 20
    rows = []
    for i in range(3):
        rows.append(
            _fill(
                fill=f"0x{i}",
                wallet=addr,
                maker=False,
                market="0xm",
                event="EV",
                one=True,
                stake="5000000",
                pct=50.0,
                bet_time=10 + i,
                net=10.0,
            )
        )
    rows.append(
        _fill(
            fill="0x9",
            wallet=addr,
            maker=False,
            market="0xm2",
            event="EV",
            one=False,
            stake="5000000",
            pct=50.0,
            bet_time=20,
            net=0.0,
            outcome=2,
        )
    )
    # profile_from_rows wants sqlite-like mappings with sport columns
    class R(dict):
        def __getitem__(self, key):
            return dict.get(self, key)

    enriched = []
    for row in rows:
        item = R(row)
        item["sport_id"] = 6
        item["sport_label"] = "Tennis"
        item["league_label"] = "ATP"
        item["game_time"] = 100
        item["market_event"] = "EV"
        enriched.append(item)
    profile = profile_from_rows("BotswanaMC", addr, enriched)  # type: ignore[arg-type]
    assert profile.scale_in_events == 1
    assert profile.two_sided_events == 1
    assert profile.tennis_fills == 4
