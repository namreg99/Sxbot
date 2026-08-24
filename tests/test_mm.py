from sxbot.filters import STYLE_MM
from sxbot.grade import format_grade, grade_paper, gradeable_rows
from sxbot.mm import MMResting, quote_pair, quote_was_hit, taker_hits_maker
from sxbot.models import Action, PublicTrade, Side, Signal
from sxbot.orderbook import analyze
from sxbot.risk import RiskGate
from sxbot.units import OddsLadder, from_percent, to_base_units
from tests.conftest import make_book, make_market, make_settings


def _mlb(**overrides):
    data = dict(
        type=226,
        sport_id=3,
        sport_label="Baseball",
        league_label="MLB",
        league_id=3,
        outcome_one="Dodgers",
        outcome_two="Pirates",
    )
    data.update(overrides)
    return make_market(**data)


def _soccer(**overrides):
    data = dict(
        type=1,
        sport_id=5,
        sport_label="Soccer",
        league_label="EPL",
        outcome_one="Arsenal",
        outcome_two="Not Arsenal",
    )
    data.update(overrides)
    return make_market(**data)


def test_quote_pair_joins_behind_both_sides() -> None:
    view = analyze(make_book(o1=((50.0, 10),), o2=((49.0, 10),)))
    pair = quote_pair(_mlb(), view, OddsLadder(125), make_settings(mm_two_sided=True))
    assert pair is not None
    assert pair.odds_one == from_percent(49.875)
    assert pair.odds_two == from_percent(48.875)
    assert pair.odds_one + pair.odds_two < 10**20


def test_quote_pair_ghosts_the_heavy_side_only() -> None:
    # 62% is baseball fav (1.40–1.80). Pick'em 50% is the red maker band.
    view = analyze(make_book(o1=((62.0, 40),), o2=((37.0, 5),)))
    pair = quote_pair(_mlb(), view, OddsLadder(125), make_settings(mm_two_sided=False, min_imbalance=0.15))
    assert pair is not None
    assert pair.odds_one == from_percent(61.875)
    assert pair.odds_two is None
    assert "mlb" in pair.reason
    pickem = analyze(make_book(o1=((50.0, 40),), o2=((49.0, 5),)))
    assert quote_pair(_mlb(), pickem, OddsLadder(125), make_settings(mm_two_sided=False, min_imbalance=0.15)) is None
    balanced = analyze(make_book(o1=((62.0, 10),), o2=((37.0, 10),)))
    assert quote_pair(_mlb(), balanced, OddsLadder(125), make_settings(mm_two_sided=False, min_imbalance=0.15)) is None


def test_quote_pair_widens_until_overround() -> None:
    # 50 / 49.5 is two-sided and not crossed. One tick behind is only ~75 bp;
    # require 100 bp so the maker has to step further back.
    view = analyze(make_book(o1=((50.0, 10),), o2=((49.5, 10),)))
    pair = quote_pair(
        _mlb(),
        view,
        OddsLadder(125),
        make_settings(mm_two_sided=True, mm_min_overround_bps=100, mm_max_widen_ticks=8),
    )
    assert pair is not None
    assert pair.odds_one + pair.odds_two <= 10**20 - (100 * 10**20 // 10_000)
    assert pair.odds_one < from_percent(49.875)


def test_quote_pair_skips_live_and_totals() -> None:
    view = analyze(make_book(o1=((50.0, 10),), o2=((49.0, 10),)))
    ladder = OddsLadder(125)
    settings = make_settings(allow_live=False)
    live = _mlb(game_time=1)
    assert quote_pair(live, view, ladder, settings, now=2) is None
    totals = _mlb(type=28, outcome_one="Over 8.5", outcome_two="Under 8.5")
    assert quote_pair(totals, view, ladder, settings) is None


def test_quote_pair_skips_one_sided_and_longshot() -> None:
    settings = make_settings(mm_two_sided=True)
    ladder = OddsLadder(125)
    one_sided = analyze(make_book(o1=((50.0, 10),), o2=()))
    assert quote_pair(_mlb(), one_sided, ladder, settings) is None
    longshot = analyze(make_book(o1=((80.0, 10),), o2=((19.0, 10),)))
    assert quote_pair(_mlb(), longshot, ladder, settings) is None


def test_quote_pair_does_not_hedge_after_one_fill() -> None:
    view = analyze(make_book(o1=((62.0, 40),), o2=((37.0, 5),)))
    resting = MMResting(odds_one=from_percent(61.875), filled_one=True)
    pair = quote_pair(_mlb(), view, OddsLadder(125), make_settings(mm_two_sided=False), resting)
    assert pair is None


def test_quote_pair_hedges_after_one_fill_when_two_sided() -> None:
    view = analyze(make_book(o1=((50.0, 10),), o2=((49.0, 10),)))
    resting = MMResting(odds_one=from_percent(49.875), filled_one=True)
    pair = quote_pair(_mlb(), view, OddsLadder(125), make_settings(mm_two_sided=True), resting)
    assert pair is not None
    assert pair.odds_one is None
    assert pair.odds_two == from_percent(48.875)


def test_tape_fill_only_when_inside_eaten() -> None:
    resting = from_percent(49.875)
    behind = analyze(make_book(o1=((50.0, 10),), o2=((49.0, 10),)))
    eaten = analyze(make_book(o1=((49.875, 10),), o2=((49.0, 10),)))
    taker_two = [
        PublicTrade(
            trade_id="t1",
            market_hash="0xabc",
            is_betting_outcome_one=False,
            stake=5_000_000,
            odds=from_percent(50.0),
            bet_time="1",
        )
    ]
    assert taker_hits_maker(Side.OUTCOME_ONE, taker_two)
    # Opposite take at the inside while we sit one tick behind is not our fill.
    assert not quote_was_hit(resting, Side.OUTCOME_ONE, behind, taker_two)
    # Same take once the inside is eaten to our ghost line.
    assert quote_was_hit(resting, Side.OUTCOME_ONE, eaten, taker_two)
    through = [
        PublicTrade(
            trade_id="t2",
            market_hash="0xabc",
            is_betting_outcome_one=False,
            stake=5_000_000,
            odds=from_percent(50.125),
            bet_time="1",
        )
    ]
    # Tape printed at/through the taker price we offered, even if TOB is still in front.
    assert quote_was_hit(resting, Side.OUTCOME_ONE, behind, through)
    assert not quote_was_hit(resting, Side.OUTCOME_ONE, behind, [])
    assert not quote_was_hit(resting, Side.OUTCOME_ONE, eaten, [])


def test_risk_allows_both_sides_when_one_side_per_market_off() -> None:
    gate = RiskGate(
        make_settings(one_side_per_market=False, max_exposure_usdc=1000, max_per_market_usdc=25)
    )
    market = _mlb(market_hash="0xmm", event_id="evt-mm")
    one = Signal(
        market=market,
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(49.875),
        reason="mm",
        mid_move_bps=0,
        imbalance=0.0,
        confidence=1.0,
        style=STYLE_MM,
    )
    two = Signal(
        market=market,
        side=Side.OUTCOME_TWO,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(48.875),
        reason="mm",
        mid_move_bps=0,
        imbalance=0.0,
        confidence=1.0,
        style=STYLE_MM,
    )
    assert gate.allow(one) is None
    gate.record(one, to_base_units(5))
    assert gate.allow(two) is None
    gate.record(two, to_base_units(5))
    assert gate.allow(one) is None


def test_hydrate_remembers_both_mm_sides() -> None:
    gate = RiskGate(make_settings(one_side_per_market=False, max_exposure_usdc=1000))
    now = 2_000
    gate.hydrate(
        [
            {
                "action": "join_maker",
                "market": "0xmm",
                "side": "outcome_one",
                "event_id": "evt-mm",
                "style": "mm",
                "game_time": now + 3_600,
                "ts": now - 10,
            },
            {
                "action": "join_maker",
                "market": "0xmm",
                "side": "outcome_two",
                "event_id": "evt-mm",
                "style": "mm",
                "game_time": now + 3_600,
                "ts": now - 9,
            },
        ],
        now=now,
    )
    assert ("0xmm", "outcome_one") in gate.joined_sides
    assert ("0xmm", "outcome_two") in gate.joined_sides
    assert "0xmm" in gate.quoted


def test_grade_scores_mm_fills_not_resting_quotes() -> None:
    rows = [
        {
            "market": "0xabc",
            "action": "join_maker",
            "style": "mm",
            "side": "outcome_one",
            "label": "Dodgers / Pirates",
            "stake": str(to_base_units(5)),
            "odds": str(from_percent(50.0)),
            "odds_pct": 50.0,
        },
        {
            "market": "0xabc",
            "action": "mm_fill",
            "style": "mm",
            "side": "outcome_one",
            "label": "Dodgers / Pirates",
            "stake": str(to_base_units(5)),
            "odds": str(from_percent(50.0)),
            "odds_pct": 50.0,
        },
        {"market": "0xabc", "action": "cancel", "style": "mm", "side": "outcome_one"},
    ]
    assert [r["action"] for r in gradeable_rows(rows)] == ["mm_fill"]
    bets = grade_paper(
        rows,
        {"0xabc": {"outcome": 1, "outcomeOneName": "Dodgers", "outcomeTwoName": "Pirates"}},
    )
    assert len(bets) == 1
    assert bets[0].result == "win"
    text = format_grade(bets)
    assert "tape" in text.lower()
    assert "assuming each quote got filled" not in text


def test_quote_pair_skips_crossed_book() -> None:
    view = analyze(make_book(o1=((52.0, 10),), o2=((49.0, 10),)))
    assert view.crossed
    assert quote_pair(_mlb(), view, OddsLadder(125), make_settings()) is None


def test_mm_holds_through_imbalance_flicker_and_one_tick_wobble() -> None:
    ladder = OddsLadder(125)
    settings = make_settings(mm_two_sided=False, min_imbalance=0.15)
    posted = from_percent(61.875)
    resting = MMResting(odds_one=posted)
    dipped = analyze(make_book(o1=((62.0, 11),), o2=((37.0, 10),)))
    held = quote_pair(_mlb(), dipped, ladder, settings, resting)
    assert held is not None
    assert held.odds_one == posted
    assert held.odds_two is None
    assert "hold" in held.reason
    wobble = analyze(make_book(o1=((62.125, 40),), o2=((37.0, 5),)))
    still = quote_pair(_mlb(), wobble, ladder, settings, resting)
    assert still is not None
    assert still.odds_one == posted
    steam = analyze(make_book(o1=((65.0, 40),), o2=((34.0, 5),)))
    moved = quote_pair(_mlb(), steam, ladder, settings, resting)
    assert moved is not None
    assert moved.odds_one == from_percent(64.875)
    assert moved.odds_one != posted


def test_mm_skips_soccer_team_vs_team() -> None:
    view = analyze(make_book(o1=((32.0, 40),), o2=((67.0, 5),)))
    market = _soccer(type=52, outcome_one="Fulham", outcome_two="Chelsea")
    assert quote_pair(market, view, OddsLadder(125), make_settings()) is None


def test_soccer_two_way_is_quoteable() -> None:
    view = analyze(make_book(o1=((55.0, 10),), o2=((44.0, 10),)))
    pair = quote_pair(_soccer(), view, OddsLadder(125), make_settings(mm_two_sided=True))
    assert pair is not None
    assert pair.odds_one is not None and pair.odds_two is not None
    # One-sided extract skips soccer pick'em (1.80–2.20 was −9% for type-1 makers).
    pickem = analyze(make_book(o1=((55.0, 40),), o2=((44.0, 5),)))
    assert quote_pair(_soccer(), pickem, OddsLadder(125), make_settings(mm_two_sided=False)) is None
    dog = analyze(make_book(o1=((32.0, 40),), o2=((67.0, 5),)))
    ghost = quote_pair(_soccer(), dog, OddsLadder(125), make_settings(mm_two_sided=False))
    assert ghost is not None
    assert ghost.odds_one == from_percent(31.875)
    assert ghost.odds_two is None
    assert "soccer" in ghost.reason
