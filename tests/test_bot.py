from sxbot.bot import RadarRow, format_radar, outcome_name, pick_universe, plain_pick_sentence, plain_picks
from sxbot.flow import FlowReport, Motive
from sxbot.models import Side
from sxbot.orderbook import analyze
from tests.conftest import make_book, make_market


def test_soonest_pregame_first_and_reserves_live_slots() -> None:
    now = 1_000
    markets = [
        make_market(market_hash="far", game_time=now + 10_000, outcome_one="Far"),
        make_market(market_hash="soon", game_time=now + 10, outcome_one="Soon"),
        make_market(market_hash="live1", game_time=now - 100, outcome_one="Live1"),
        make_market(market_hash="live2", game_time=now - 50, outcome_one="Live2"),
    ]
    picked = pick_universe(markets, cap=2, now=now, watch_live=True)
    hashes = [m.market_hash for m in picked]
    assert hashes[0] == "soon"
    assert "live1" in hashes or "live2" in hashes
    assert "far" not in hashes


def test_watch_live_off_drops_in_play() -> None:
    now = 1_000
    markets = [
        make_market(market_hash="soon", game_time=now + 10),
        make_market(market_hash="live1", game_time=now - 100),
    ]
    picked = pick_universe(markets, cap=5, now=now, watch_live=False)
    assert [m.market_hash for m in picked] == ["soon"]


def test_pick_universe_prefers_quoteable_sports() -> None:
    now = 1_000
    npb = make_market(
        market_hash="npb",
        game_time=now + 10,
        sport_id=3,
        type=52,
        league_id=19,
        league_label="NPB",
        sport_label="Baseball",
    )
    mlb = make_market(
        market_hash="mlb",
        game_time=now + 100,
        sport_id=3,
        type=226,
        league_label="MLB",
        sport_label="Baseball",
        league_id=3,
    )
    from sxbot.filters import quote_family

    picked = pick_universe([npb, mlb], cap=1, now=now, watch_live=False, prefer=quote_family)
    assert [m.market_hash for m in picked] == ["mlb"]


def _radar_row(*, confidence: float) -> RadarRow:
    view = analyze(make_book(o1=((53.0, 10),), o2=((46.0, 10),)))
    report = FlowReport(
        motive=Motive.MAKER_STEAM,
        side=Side.OUTCOME_ONE,
        move_bps=80,
        persistence=0.4,
        tob_vs_dw_bps=None,
        tape_prints=0,
        steam_hits=1,
        confidence=confidence,
        reasons=("makers shifted mid",),
    )
    return RadarRow(market=make_market(), view=view, report=report)


def test_format_radar_empty() -> None:
    text = format_radar([])
    assert "No flow yet" in text


def test_format_radar_ranks_by_confidence() -> None:
    rows = [_radar_row(confidence=0.6), _radar_row(confidence=0.9)]
    text = format_radar(rows)
    first_idx = text.index("conf=0.90")
    second_idx = text.index("conf=0.60")
    assert first_idx < second_idx
    assert "1 actionable / 2 flagged" not in text  # both are actionable here
    assert "2 actionable / 2 flagged" in text


def test_outcome_name_picks_the_right_side() -> None:
    market = make_market(outcome_one="Rams", outcome_two="49ers")
    assert outcome_name(market, Side.OUTCOME_ONE) == "Rams"
    assert outcome_name(market, Side.OUTCOME_TWO) == "49ers"


def test_plain_pick_sentence_is_readable_and_names_the_team() -> None:
    row = _radar_row(confidence=0.93)
    sentence = plain_pick_sentence(row)
    assert row.market.outcome_one in sentence  # side is OUTCOME_ONE in the fixture
    assert "market makers" in sentence
    assert "very strong" in sentence
    assert "0.93" in sentence


def test_plain_picks_ranked_and_capped() -> None:
    rows = [_radar_row(confidence=c) for c in (0.5, 0.95, 0.8, 0.65, 0.99, 0.61)]
    picks = plain_picks(rows, limit=3)
    assert len(picks) == 3
    assert "0.99" in picks[0]
    assert "0.95" in picks[1]
    assert "0.80" in picks[2]


def test_format_radar_leads_with_top_picks() -> None:
    rows = [_radar_row(confidence=0.9)]
    text = format_radar(rows)
    assert text.index("TOP PICKS") < text.index("Full detail")
