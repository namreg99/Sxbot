from sxbot.board import (
    _fill_view,
    _record,
    build_snapshot,
    render_html,
    render_text,
    unique_open_rows,
)
from sxbot.units import from_percent
from tests.conftest import make_settings


class FakeClient:
    def __init__(self, markets=None) -> None:
        self.markets = markets or []

    def find_markets(self, hashes):
        return list(self.markets)

    def v2_public_trades(self, **kwargs):
        return []


def test_unique_open_rows_drops_cancel_and_keeps_last_join() -> None:
    rows = [
        {"action": "join_maker", "market": "0x1", "side": "outcome_one", "ts": 1},
        {"action": "join_maker", "market": "0x1", "side": "outcome_one", "ts": 2, "odds_pct": 60},
        {"action": "cancel", "market": "0x1", "side": "outcome_one", "ts": 3},
        {"action": "join_maker", "market": "0x2", "side": "outcome_two", "ts": 4},
    ]
    unique = unique_open_rows(rows)
    assert len(unique) == 1
    assert unique[0]["market"] == "0x2"


def test_fill_view_names_the_stored_side() -> None:
    raw = {
        "betTime": 1_700_000_000,
        "bettor": "0xfd3a22179ca4104e97c670b834750bd7ff310b17",
        "maker": False,
        "stake": 6_200_000_000,
        "odds": str(from_percent(51.25)),
        "bettingOutcomeOne": False,
        "marketHash": "0xabc",
        "settled": True,
        "outcome": 2,
        "settleNetReturnValue": 12_000.0,
    }
    market = {
        "marketHash": "0xabc",
        "outcomeOneName": "Dodgers",
        "outcomeTwoName": "Pirates",
        "leagueLabel": "MLB",
        "outcome": 2,
        "gameTime": 1_700_000_100,
        "teamOneScore": 4,
        "teamTwoScore": 3,
    }
    view = _fill_view(raw, market, {"0xfd3a22179ca4104e97c670b834750bd7ff310b17": "GambleGuruGary"})
    assert view["who"] == "GambleGuruGary"
    assert view["picked"] == "Pirates"
    assert view["result"] == "win"
    assert view["role"] == "taker"
    assert view["stake_usdc"] == 6200.0


def test_board_snapshot_and_html(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    paper.write_text(
        (
            '{"ts": 10, "action": "join_maker", "market": "0xabc", "side": "outcome_one",'
            ' "label": "Uchijima / Hercog", "league": "WTA", "odds": "%s",'
            ' "odds_pct": 71.875, "stake": "5000000", "stake_usdc": 5,'
            ' "outcome_one": "Uchijima", "outcome_two": "Hercog", "style": "tennis_short",'
            ' "motive": "maker_steam", "game_time": 100}\n'
        )
        % from_percent(71.875),
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {
                "marketHash": "0xabc",
                "outcomeOneName": "Uchijima",
                "outcomeTwoName": "Hercog",
                "leagueLabel": "WTA",
                "outcome": 1,
                "gameTime": 100,
            }
        ]
    )
    snap = build_snapshot(
        client,
        make_settings(paper_log=str(paper)),
        tape_rows=[],
    )
    assert snap["record"]["wins"] == 1
    assert snap["record"]["losses"] == 0
    assert snap["best_priced"]["wins"] == 1
    assert snap["best_open"] is None
    page = render_html(snap)
    assert "sxbot board" in page
    assert "Uchijima" in page
    assert "Dodgers" not in page
    text = render_text(snap)
    assert "best priced settled" in text
    assert "picked Uchijima" in text


def test_best_open_is_the_shortest_pending(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    a = from_percent(52.0)
    b = from_percent(70.0)
    paper.write_text(
        "\n".join(
            [
                '{"ts": 1, "action": "join_maker", "market": "0x1", "side": "outcome_one",'
                f' "label": "A / B", "league": "MLB", "odds": "{a}", "odds_pct": 52.0,'
                ' "stake": "5000000", "outcome_one": "A", "outcome_two": "B", "style": "mlb"}',
                '{"ts": 2, "action": "join_maker", "market": "0x2", "side": "outcome_one",'
                f' "label": "C / D", "league": "ATP", "odds": "{b}", "odds_pct": 70.0,'
                ' "stake": "5000000", "outcome_one": "C", "outcome_two": "D", "style": "tennis_short"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {"marketHash": "0x1", "outcomeOneName": "A", "outcomeTwoName": "B", "leagueLabel": "MLB"},
            {"marketHash": "0x2", "outcomeOneName": "C", "outcomeTwoName": "D", "leagueLabel": "ATP"},
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    assert snap["best_open"]["picked"] == "C"
    assert snap["best_open"]["odds_pct"] == 70.0


def test_record_win_pct() -> None:
    rec = _record(
        [
            {"result": "win"},
            {"result": "win"},
            {"result": "lose"},
            {"result": "pending"},
        ]
    )
    assert rec["wins"] == 2
    assert rec["losses"] == 1
    assert rec["pending"] == 1
    assert rec["win_pct"] == 66.7
