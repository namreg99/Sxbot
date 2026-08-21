from sxbot.grade import format_grade, grade_paper, grade_row
from sxbot.units import from_percent, to_base_units


def _paper(**overrides):
    row = {
        "market": "0xabc",
        "label": "Rams / 49ers",
        "league": "NFL",
        "side": "outcome_one",
        "action": "join_maker",
        "motive": "maker_steam",
        "stake": str(to_base_units(5)),
        "stake_usdc": 5.0,
        "odds": str(from_percent(50.0)),
        "odds_pct": 50.0,
        "game_time": 1,
    }
    row.update(overrides)
    return row


def test_even_money_win() -> None:
    market = {"outcome": 1, "outcomeOneName": "Rams", "outcomeTwoName": "49ers", "gameTime": 1}
    bet = grade_row(_paper(), market)
    assert bet.result == "win"
    assert bet.pnl_usdc == 5.0


def test_lose_and_void_and_pending() -> None:
    assert grade_row(_paper(), {"outcome": 2}).result == "lose"
    assert grade_row(_paper(), {"outcome": 2}).pnl_usdc == -5.0
    assert grade_row(_paper(), {"outcome": 0}).result == "void"
    assert grade_row(_paper(), {"outcome": 0}).pnl_usdc == 0.0
    assert grade_row(_paper(), {"outcome": None, "gameTime": 9}).result == "pending"
    assert grade_row(_paper(), None).result == "missing"


def test_outcome_two_win() -> None:
    bet = grade_row(_paper(side="outcome_two"), {"outcome": 2, "outcomeTwoName": "49ers"})
    assert bet.result == "win"


def test_format_grade_pending_copy() -> None:
    bets = grade_paper(
        [_paper()],
        {"0xabc": {"outcome": None, "gameTime": 2_000_000_000, "leagueLabel": "NFL"}},
    )
    text = format_grade(bets)
    assert "still pending" in text
    assert "NOT a rewind" in text
    assert "No games in this log have been reported yet" in text
