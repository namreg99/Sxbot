from sxbot.scoreboard import (
    aggregate_by,
    confidence_bucket,
    format_scoreboard,
    grade_flow,
    grade_flow_row,
)


def _row(**overrides):
    row = {
        "market": "0xabc",
        "label": "Rams / 49ers",
        "league": "NFL",
        "phase": "pregame",
        "motive": "maker_steam",
        "side": "outcome_one",
        "confidence": 0.7,
        "mid_pct": 55.0,
        "game_time": 1,
    }
    row.update(overrides)
    return row


def test_confidence_bucket_boundaries() -> None:
    assert confidence_bucket(0.0) == "<0.60"
    assert confidence_bucket(0.59) == "<0.60"
    assert confidence_bucket(0.60) == "0.60-0.75"
    assert confidence_bucket(0.75) == "0.75-0.90"
    assert confidence_bucket(0.90) == ">=0.90"
    assert confidence_bucket(0.99) == ">=0.90"


def test_win_beats_the_implied_price() -> None:
    # Flagged side was quoted at 55% and it won: edge is the surprise, +0.45.
    bet = grade_flow_row(_row(), {"outcome": 1})
    assert bet.result == "win"
    assert bet.edge is not None
    assert round(bet.edge, 2) == 0.45


def test_loss_is_negative_edge() -> None:
    bet = grade_flow_row(_row(), {"outcome": 2})
    assert bet.result == "lose"
    assert round(bet.edge, 2) == -0.55


def test_outcome_two_side_flips_the_implied_probability() -> None:
    bet = grade_flow_row(_row(side="outcome_two", mid_pct=55.0), {"outcome": 2})
    # implied prob of outcome_two = 1 - 0.55 = 0.45; won -> edge = 1 - 0.45
    assert bet.result == "win"
    assert round(bet.edge, 2) == 0.55


def test_void_has_no_edge() -> None:
    bet = grade_flow_row(_row(), {"outcome": 0})
    assert bet.result == "void"
    assert bet.edge is None


def test_pending_and_missing() -> None:
    assert grade_flow_row(_row(), {"outcome": None, "gameTime": 9}).result == "pending"
    assert grade_flow_row(_row(), None).result == "missing"


def test_missing_mid_pct_still_grades_result_without_edge() -> None:
    bet = grade_flow_row(_row(mid_pct=None), {"outcome": 1})
    assert bet.result == "win"
    assert bet.edge is None


def test_aggregate_by_motive() -> None:
    bets = grade_flow(
        [_row(motive="maker_steam"), _row(motive="maker_steam", side="outcome_two"), _row(motive="size_rotation")],
        {"0xabc": {"outcome": 1}},
    )
    by_motive = aggregate_by(bets, lambda b: b.motive)
    assert by_motive["maker_steam"].n == 2
    assert by_motive["size_rotation"].n == 1
    # one win (outcome_one) + one loss (outcome_two) in maker_steam
    assert by_motive["maker_steam"].wins == 1
    assert by_motive["maker_steam"].losses == 1


def test_format_scoreboard_handles_empty() -> None:
    text = format_scoreboard([])
    assert "No flow logged yet" in text


def test_format_scoreboard_small_sample_caveat() -> None:
    bets = grade_flow([_row()], {"0xabc": {"outcome": 1}})
    text = format_scoreboard(bets)
    assert "hit rate alone is misleading" in text
    assert "Treat every number above as noise" in text


def test_scoreboard_matches_mixed_case_hash() -> None:
    bets = grade_flow([_row(market="0xABC")], {"0xabc": {"outcome": 1}})
    assert bets[0].result == "win"


def test_scoreboard_notes_started_but_unreported() -> None:
    bets = grade_flow(
        [_row(game_time=1, mid_pct=None)],
        {"0xabc": {"outcome": None, "gameTime": 1}},
    )
    text = format_scoreboard(bets)
    assert "SX has not reported" in text
