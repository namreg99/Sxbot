from sxbot.grade import format_grade, grade_paper, grade_row, take_of_make
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


def test_take_of_make_is_the_other_team_at_complement_odds() -> None:
    odds = from_percent(62.5)  # Cincy 1.60
    result, pnl = take_of_make(make_odds=odds, make_result="lose", stake_usdc=5)
    assert result == "win"
    assert pnl == 8.33  # $5 at 2.67 on SF
    result, pnl = take_of_make(make_odds=odds, make_result="win", stake_usdc=5)
    assert result == "lose"
    assert pnl == -5.0


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
    assert bet.picked == "49ers"


def test_format_grade_names_the_picked_side() -> None:
    bets = grade_paper(
        [_paper(side="outcome_two", label="Dodgers / Pirates")],
        {"0xabc": {"outcome": 1, "outcomeOneName": "Dodgers", "outcomeTwoName": "Pirates",
                   "teamOneScore": 4, "teamTwoScore": 3}},
    )
    text = format_grade(bets)
    assert "picked Pirates" in text
    assert "LOSE" in text


def test_picked_uses_paper_row_when_market_omits_names() -> None:
    bet = grade_row(
        _paper(
            side="outcome_two",
            label="Los Angeles Dodgers / Pittsburgh Pirates",
            outcome_one="Los Angeles Dodgers",
            outcome_two="Pittsburgh Pirates",
        ),
        {"outcome": 1},
    )
    assert bet.result == "lose"
    assert bet.picked == "Pittsburgh Pirates"


def test_format_grade_pending_copy() -> None:
    bets = grade_paper(
        [_paper()],
        {"0xabc": {"outcome": None, "gameTime": 2_000_000_000, "leagueLabel": "NFL"}},
    )
    text = format_grade(bets)
    assert "still pending" in text
    assert "NOT a rewind" in text
    assert "SX-reported outcome" in text
    assert "waiting on kickoff" in text


def test_grades_when_hash_case_differs() -> None:
    bets = grade_paper(
        [_paper(market="0xABC")],
        {"0xabc": {"outcome": 1, "outcomeOneName": "Rams"}},
    )
    assert bets[0].result == "win"


def test_string_outcome_and_omitted_outcome() -> None:
    assert grade_row(_paper(), {"outcome": "1", "outcomeOneName": "Rams"}).result == "win"
    assert grade_row(_paper(), {"status": "ACTIVE"}).result == "pending"


def test_format_grade_splits_in_play_from_future_kickoff() -> None:
    bets = grade_paper(
        [
            _paper(label="Over 8.5 / Under 8.5", league="KBO League", game_time=100),
            _paper(label="Rams / 49ers", league="NFL", game_time=9_000, market="0xdef"),
        ],
        {
            "0xabc": {"outcome": None, "gameTime": 100, "leagueLabel": "KBO League"},
            "0xdef": {"outcome": None, "gameTime": 9_000, "leagueLabel": "NFL"},
        },
    )
    text = format_grade(bets, now=1_000)
    assert "waiting on SX report" in text
    assert "Over 8.5" in text
    kickoff_block = text.split("waiting on kickoff", 1)[1]
    assert "Rams / 49ers" in kickoff_block
    assert "Over 8.5" not in kickoff_block


def test_format_grade_notes_stacked_joins() -> None:
    bets = grade_paper(
        [_paper(), _paper(), _paper(market="0xzzz", label="Other / Line")],
        {
            "0xabc": {"outcome": 1, "outcomeOneName": "Rams"},
            "0xzzz": {"outcome": None, "gameTime": 9_000},
        },
    )
    text = format_grade(bets, now=1_000)
    assert "stacked joins" in text
