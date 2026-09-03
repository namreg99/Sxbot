from sxbot.board import (
    LIVE_TEST_TRADES,
    _fill_view,
    _record,
    _tape_windows,
    _windows,
    attach_clv,
    build_snapshot,
    clv_record,
    live_test_status,
    make_take_line,
    paper_record,
    render_html,
    render_text,
    scrape_big_fills,
    unique_lifetime_rows,
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


def test_unique_lifetime_rows_keep_kickoff_cancel() -> None:
    rows = [
        {"action": "join_maker", "market": "0x1", "side": "outcome_one", "ts": 1, "odds_pct": 60},
        {"action": "cancel", "market": "0x1", "side": "outcome_one", "ts": 3},
        {"action": "join_maker", "market": "0x2", "side": "outcome_two", "ts": 4},
    ]
    life = unique_lifetime_rows(rows)
    hashes = {row["market"] for row in life}
    assert hashes == {"0x1", "0x2"}
    kept = next(row for row in life if row["market"] == "0x1")
    assert kept["action"] == "join_maker"
    assert kept["odds_pct"] == 60


def test_unique_rows_keep_follow_and_mm_on_same_side() -> None:
    rows = [
        {"action": "join_maker", "market": "0x1", "side": "outcome_two", "style": "soccer", "ts": 1},
        {"action": "join_maker", "market": "0x1", "side": "outcome_two", "style": "mm", "ts": 2},
        {"action": "cancel", "market": "0x1", "side": "outcome_two", "style": "soccer", "ts": 3},
    ]
    open_rows = unique_open_rows(rows)
    assert len(open_rows) == 1
    assert open_rows[0]["style"] == "mm"
    life = unique_lifetime_rows(rows)
    styles = {row["style"] for row in life}
    assert styles == {"soccer", "mm"}


def test_tape_windows_newest_first_and_cover() -> None:
    start, end = 0, 15 * 3600
    wins = _tape_windows(start, end)
    assert wins[0][1] == end
    assert wins[0][0] == end - 600
    assert wins[-1][0] == start
    covered = 0
    prev_end = end
    for w0, w1 in wins:
        assert w1 == prev_end
        covered += w1 - w0
        prev_end = w0
    assert covered == end - start
    oldest = _windows(0, 9, 3)
    newest = _windows(0, 9, 3, newest_first=True)
    assert oldest == [(0, 3), (3, 6), (6, 9)]
    assert newest == [(6, 9), (3, 6), (0, 3)]


def test_scrape_newest_first_and_deadline() -> None:
    class Rec:
        def __init__(self) -> None:
            self.starts: list[int] = []

        def v2_public_trades(self, **kwargs):
            self.starts.append(int(kwargs["start_date"]))
            return []

    rec = Rec()
    scrape_big_fills(rec, start=0, end=9, min_usdc=1, step=3, newest_first=False, max_pages=1)
    assert rec.starts == [0, 3, 6]
    rec.starts.clear()
    scrape_big_fills(rec, start=0, end=9, min_usdc=1, newest_first=True, deadline=0, max_pages=1)
    assert rec.starts == []


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
    assert snap["live_test"]["target"] == LIVE_TEST_TRADES
    assert snap["live_test"]["ready"] is False
    assert snap["live_test"]["settled"] == 1
    page = render_html(snap)
    assert "sxbot board" in page
    assert "Uchijima" in page
    assert "Dodgers" not in page
    assert "live-test gate" in page
    text = render_text(snap)
    assert "follow best priced" in text
    assert "MAKE $5 Uchijima" in text
    assert "fills when someone bets Hercog" in text
    assert "TAKE $5 Hercog" not in text
    assert "taker bot unique" in text
    assert "maker bot unique" in text
    assert "live-test gate" in text


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
    assert rec["roi_pct"] is None


def test_record_roi_uses_settled_stake() -> None:
    rec = _record(
        [
            {"result": "win", "stake_usdc": 5, "pnl_usdc": 5},
            {"result": "lose", "stake_usdc": 5, "pnl_usdc": -5},
            {"result": "pending", "stake_usdc": 5, "pnl_usdc": None},
        ]
    )
    assert rec["wins"] == 1
    assert rec["losses"] == 1
    assert rec["roi_pct"] == 0.0
    assert rec["stake_usdc"] == 10.0


def test_maker_ev_is_parked_size_not_a_fade() -> None:
    from sxbot.board import is_maker_ev

    assert is_maker_ev({"motive": "maker_steam", "side": "outcome_one", "imbalance": 0})
    assert is_maker_ev({"side": "outcome_two", "imbalance": -0.4})
    assert not is_maker_ev({"side": "outcome_one", "imbalance": -0.4, "motive": "tob_lag"})


def test_follow_buckets_split_priced_vs_ev(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    short = from_percent(70.0)
    longshot = from_percent(40.0)
    paper.write_text(
        "\n".join(
            [
                '{"ts": 1, "action": "join_maker", "market": "0x1", "side": "outcome_one",'
                f' "label": "A / B", "league": "EPL", "odds": "{short}", "odds_pct": 70.0,'
                ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "A", "outcome_two": "B",'
                ' "style": "soccer", "motive": "maker_steam", "imbalance": 0.4, "game_time": 10}',
                '{"ts": 2, "action": "join_maker", "market": "0x2", "side": "outcome_one",'
                f' "label": "C / D", "league": "ATP", "odds": "{longshot}", "odds_pct": 40.0,'
                ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "C", "outcome_two": "D",'
                ' "style": "tennis_dog", "motive": "tob_lag", "imbalance": -0.5, "game_time": 10}',
                '{"ts": 3, "action": "join_maker", "market": "0xmm", "side": "outcome_one",'
                f' "label": "E / F", "league": "MLB", "odds": "{short}", "odds_pct": 70.0,'
                ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "E", "outcome_two": "F",'
                ' "style": "mm", "motive": "mm_quote", "imbalance": 0.4, "game_time": 10}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {"marketHash": "0x1", "outcomeOneName": "A", "outcomeTwoName": "B", "leagueLabel": "EPL", "outcome": 1, "gameTime": 10},
            {"marketHash": "0x2", "outcomeOneName": "C", "outcomeTwoName": "D", "leagueLabel": "ATP", "outcome": 2, "gameTime": 10},
            {"marketHash": "0xmm", "outcomeOneName": "E", "outcomeTwoName": "F", "leagueLabel": "MLB", "outcome": 1, "gameTime": 10},
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    assert snap["best_priced"]["wins"] == 1
    assert snap["best_priced"]["losses"] == 0
    assert snap["maker_ev"]["wins"] == 1
    assert snap["maker_ev"]["losses"] == 0
    assert snap["priced_ev"]["wins"] == 1
    assert snap["follow_record"]["wins"] == 1
    assert snap["follow_record"]["losses"] == 1
    assert snap["record"]["wins"] == 2


def test_lifetime_record_keeps_settled_after_cancel(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    odds = from_percent(70.0)
    paper.write_text(
        "\n".join(
            [
                '{"ts": 1, "action": "join_maker", "market": "0xabc", "side": "outcome_one",'
                f' "label": "Chelsea / Not Chelsea", "league": "EPL", "odds": "{odds}",'
                ' "odds_pct": 70.0, "stake": "5000000", "stake_usdc": 5,'
                ' "outcome_one": "Chelsea", "outcome_two": "Not Chelsea", "style": "soccer",'
                ' "game_time": 10}',
                '{"ts": 20, "action": "cancel", "market": "0xabc", "side": "outcome_one",'
                ' "label": "Chelsea / Not Chelsea", "league": "EPL",'
                f' "odds": "{odds}", "odds_pct": 70.0, "stake": "5000000",'
                ' "outcome_one": "Chelsea", "outcome_two": "Not Chelsea", "style": "soccer"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {
                "marketHash": "0xabc",
                "outcomeOneName": "Chelsea",
                "outcomeTwoName": "Not Chelsea",
                "leagueLabel": "EPL",
                "outcome": 1,
                "gameTime": 10,
            }
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    assert snap["record"]["wins"] == 1
    assert snap["record"]["losses"] == 0
    assert snap["open"] == []
    assert snap["best_priced"]["wins"] == 1
    assert "combined unique" in render_text(snap)
    assert "taker bot unique" in render_text(snap)


def test_dual_books_kelly_skip_is_not_a_loss(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    short = from_percent(50.0)
    paper.write_text(
        "\n".join(
            [
                '{"ts": 1, "action": "join_maker", "market": "0x1", "side": "outcome_one",'
                f' "label": "A / B", "league": "EPL", "odds": "{short}", "odds_pct": 50.0,'
                ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "A", "outcome_two": "B",'
                ' "style": "soccer", "motive": "maker_steam", "imbalance": 0.4,'
                ' "fair_pct": 55.0, "flat_stake_usdc": 5, "game_time": 10}',
                '{"ts": 2, "action": "join_maker", "market": "0x2", "side": "outcome_one",'
                f' "label": "C / D", "league": "ATP", "odds": "{short}", "odds_pct": 50.0,'
                ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "C", "outcome_two": "D",'
                ' "style": "tennis_dog", "motive": "tob_lag", "imbalance": -0.5, "game_time": 10}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {"marketHash": "0x1", "outcomeOneName": "A", "outcomeTwoName": "B", "leagueLabel": "EPL", "outcome": 1, "gameTime": 10},
            {"marketHash": "0x2", "outcomeOneName": "C", "outcomeTwoName": "D", "leagueLabel": "ATP", "outcome": 2, "gameTime": 10},
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    rec = snap["record"]
    assert rec["wins"] == 1
    assert rec["losses"] == 1
    assert rec["flat"]["wins"] == 1
    assert rec["flat"]["losses"] == 1
    assert rec["kelly"]["wins"] == 1
    assert rec["kelly"]["losses"] == 0
    assert rec["kelly"]["skipped"] == 1
    assert rec["kelly"]["stake_usdc"] == 25.0
    assert snap["live_test"]["settled"] == 2
    assert snap["live_test"]["remaining"] == LIVE_TEST_TRADES - 2
    assert snap["live_test"]["ready"] is False
    page = render_html(snap)
    assert "Kelly 1–0" in page
    assert "skip 1" in page


def test_live_test_ready_needs_100_and_both_books_green() -> None:
    rec = {
        "wins": 60,
        "losses": 40,
        "roi_pct": 5.0,
        "flat": {"wins": 60, "losses": 40, "roi_pct": 5.0},
        "kelly": {"wins": 50, "losses": 30, "roi_pct": 8.0, "skipped": 20},
    }
    gate = live_test_status(rec, follow={"wins": 20, "losses": 10}, mm={"wins": 40, "losses": 30})
    assert gate["ready"] is True
    assert gate["settled"] == 100
    assert gate["follow_settled"] == 30
    assert gate["mm_settled"] == 70
    rec["flat"]["roi_pct"] = -1.0
    assert live_test_status(rec)["ready"] is False
    rec["flat"]["roi_pct"] = 5.0
    rec["kelly"]["roi_pct"] = -2.0
    assert live_test_status(rec)["ready"] is False
    short = {
        "wins": 17,
        "losses": 8,
        "roi_pct": 14.5,
        "flat": {"roi_pct": 14.5},
        "kelly": {"wins": 12, "losses": 5, "roi_pct": 8.0, "skipped": 8},
    }
    assert live_test_status(short)["ready"] is False
    assert live_test_status(short)["remaining"] == LIVE_TEST_TRADES - 25


def test_paper_record_scales_kelly_pnl() -> None:
    views = [
        {
            "result": "win",
            "stake_usdc": 5,
            "pnl_usdc": 5,
            "flat_stake_usdc": 5,
            "flat_pnl_usdc": 5,
            "kelly_stake_usdc": 25,
            "kelly_pnl_usdc": 25,
        },
        {
            "result": "lose",
            "stake_usdc": 5,
            "pnl_usdc": -5,
            "flat_stake_usdc": 5,
            "flat_pnl_usdc": -5,
            "kelly_stake_usdc": None,
            "kelly_pnl_usdc": None,
        },
    ]
    rec = paper_record(views)
    assert rec["flat"]["roi_pct"] == 0.0
    assert rec["kelly"]["wins"] == 1
    assert rec["kelly"]["losses"] == 0
    assert rec["kelly"]["skipped"] == 1
    assert rec["kelly"]["pnl_usdc"] == 25.0


def test_maker_quote_names_the_quoted_side_and_the_mirror_fill(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    odds = from_percent(40.816)  # SF @ ~2.45 as maker
    paper.write_text(
        (
            '{"ts": 1, "action": "join_maker", "market": "0xml", "side": "outcome_one",'
            f' "label": "San Francisco Giants / Cincinnati Reds", "league": "MLB",'
            f' "odds": "{odds}", "odds_pct": 40.816, "stake": "5000000", "stake_usdc": 5,'
            ' "outcome_one": "San Francisco Giants", "outcome_two": "Cincinnati Reds",'
            ' "style": "mm", "motive": "mm_quote", "imbalance": 0.3, "game_time": 10}\n'
        ),
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {
                "marketHash": "0xml",
                "outcomeOneName": "San Francisco Giants",
                "outcomeTwoName": "Cincinnati Reds",
                "leagueLabel": "MLB",
            }
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    open_row = snap["open"][0]
    assert open_row["picked"] == "San Francisco Giants"
    assert open_row["verb"] == "make"
    assert open_row["fill_when"] == "Cincinnati Reds"
    line = make_take_line(open_row)
    assert "MAKE $5 San Francisco Giants" in line
    assert "fills when someone bets Cincinnati Reds" in line
    assert "TAKE $5 Cincinnati Reds" not in line
    page = render_html(snap)
    assert "MAKE $5 San Francisco Giants" in page
    assert "fills when someone bets Cincinnati Reds" in page
    text = render_text(snap)
    assert "maker bot unique" in text
    assert "TAKE $5 Cincinnati Reds" not in text


def test_maker_unique_scores_at_maker_odds_not_the_mirror(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    odds = from_percent(62.5)
    paper.write_text(
        (
            '{"ts": 1, "action": "join_maker", "market": "0xml", "side": "outcome_two",'
            f' "label": "San Francisco Giants / Cincinnati Reds", "league": "MLB",'
            f' "odds": "{odds}", "odds_pct": 62.5, "stake": "5000000", "stake_usdc": 5,'
            ' "outcome_one": "San Francisco Giants", "outcome_two": "Cincinnati Reds",'
            ' "style": "mm", "motive": "mm_quote", "game_time": 10}\n'
        ),
        encoding="utf-8",
    )
    # Giants win → maker Cincy lost $5. That is the maker bot's book.
    # The taker bot is a different process and is not this flipped complement.
    client = FakeClient(
        [
            {
                "marketHash": "0xml",
                "outcomeOneName": "San Francisco Giants",
                "outcomeTwoName": "Cincinnati Reds",
                "leagueLabel": "MLB",
                "outcome": 1,
                "gameTime": 10,
            }
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    rec = snap["mm_record"]
    assert rec["wins"] == 0
    assert rec["losses"] == 1
    assert rec["pnl_usdc"] == -5.0
    assert "take" not in rec
    assert snap["follow_record"]["n"] == 0


def test_clv_is_closing_prob_of_our_side_minus_our_price() -> None:
    closes = {"0xm": 60.0}  # outcome one closed at 60%
    ours = attach_clv({"market": "0xm", "side": "outcome_one", "odds_pct": 55.0}, closes)
    # bet O1 at 55%, closed 60% -> we beat the close by 5 points
    assert ours["clv_pct"] == 5.0
    other = attach_clv({"market": "0xm", "side": "outcome_two", "odds_pct": 45.0}, closes)
    # bet O2 at 45%, O2 closed 40% -> we paid 5 points worse than close
    assert other["clv_pct"] == -5.0
    missing = attach_clv({"market": "0xnothere", "side": "outcome_one", "odds_pct": 50.0}, closes)
    assert missing["clv_pct"] is None
    rec = clv_record([ours, other, missing])
    assert rec["n"] == 2
    assert rec["avg_clv_pct"] == 0.0
    assert rec["beat_close"] == 1
    assert rec["lost_close"] == 1


def test_snapshot_stamps_clv_from_closes_log(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    odds = from_percent(55.0)
    paper.write_text(
        (
            '{"ts": 1, "action": "join_maker", "market": "0xm", "side": "outcome_one",'
            f' "label": "A / B", "league": "MLB", "odds": "{odds}", "odds_pct": 55.0,'
            ' "stake": "5000000", "stake_usdc": 5, "outcome_one": "A", "outcome_two": "B",'
            ' "style": "mlb", "motive": "maker_steam", "game_time": 10}\n'
        ),
        encoding="utf-8",
    )
    closes = tmp_path / "sxbot-closes.jsonl"
    closes.write_text(
        '{"action": "close", "market": "0xm", "close_mid_pct": 60.0}\n', encoding="utf-8"
    )
    client = FakeClient([{"marketHash": "0xm", "outcomeOneName": "A", "outcomeTwoName": "B"}])
    snap = build_snapshot(
        client,
        make_settings(paper_log=str(paper), closes_log=str(closes)),
        tape_rows=[],
    )
    assert snap["follow_clv"]["n"] == 1
    assert snap["follow_clv"]["avg_clv_pct"] == 5.0
    assert snap["follow_clv"]["beat_close"] == 1
    page = render_html(snap)
    assert "taker CLV" in page
    text = render_text(snap)
    assert "taker CLV vs close" in text


def test_take_flow_is_the_same_team_as_makers(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    odds = from_percent(54.0)  # pay the spread to get Cincy with the steam
    paper.write_text(
        (
            '{"ts": 1, "action": "take_flow", "market": "0xml", "side": "outcome_two",'
            f' "label": "San Francisco Giants / Cincinnati Reds", "league": "MLB",'
            f' "odds": "{odds}", "odds_pct": 54.0, "stake": "5000000", "stake_usdc": 5,'
            ' "outcome_one": "San Francisco Giants", "outcome_two": "Cincinnati Reds",'
            ' "style": "mlb", "motive": "maker_steam", "game_time": 10}\n'
        ),
        encoding="utf-8",
    )
    client = FakeClient(
        [
            {
                "marketHash": "0xml",
                "outcomeOneName": "San Francisco Giants",
                "outcomeTwoName": "Cincinnati Reds",
                "leagueLabel": "MLB",
            }
        ]
    )
    snap = build_snapshot(client, make_settings(paper_log=str(paper)), tape_rows=[])
    open_row = snap["open"][0]
    assert open_row["picked"] == "Cincinnati Reds"
    assert open_row["verb"] == "take"
    line = make_take_line(open_row)
    assert "TAKE $5 Cincinnati Reds" in line
    assert "same side as makers" in line
    assert "TAKE $5 San Francisco Giants" not in line


def test_bot_you_together_stay_separate(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    manual = tmp_path / "sxbot-manual.jsonl"
    odds = from_percent(71.875)
    paper.write_text(
        (
            '{"ts": 10, "action": "join_maker", "market": "0xabc", "side": "outcome_one",'
            f' "label": "Uchijima / Hercog", "league": "WTA", "odds": "{odds}",'
            ' "odds_pct": 71.875, "stake": "5000000", "stake_usdc": 5,'
            ' "outcome_one": "Uchijima", "outcome_two": "Hercog", "style": "tennis_short",'
            ' "motive": "maker_steam", "game_time": 100}\n'
        ),
        encoding="utf-8",
    )
    you_odds = from_percent(50.0)
    manual.write_text(
        (
            '{"ts": 20, "ticket_id": "abc123", "source": "manual", "action": "manual",'
            f' "style": "manual", "side": "outcome_two", "market": "0xabc",'
            f' "label": "Uchijima / Hercog", "league": "WTA", "picked": "Hercog",'
            f' "outcome_one": "Uchijima", "outcome_two": "Hercog", "odds": "{you_odds}",'
            ' "odds_pct": 50.0, "decimal": 2.0, "stake": "25000000", "stake_usdc": 25,'
            ' "book": "pinnacle", "motive": "manual", "game_time": 100}\n'
        ),
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
        make_settings(paper_log=str(paper), manual_log=str(manual)),
        tape_rows=[],
    )
    assert snap["follow_record"]["wins"] == 1
    assert snap["follow_record"]["losses"] == 0
    assert snap["you_record"]["wins"] == 0
    assert snap["you_record"]["losses"] == 1
    assert snap["you_record"]["pnl_usdc"] == -25.0
    assert snap["together_record"]["wins"] == 1
    assert snap["together_record"]["losses"] == 1
    assert snap["bot_book"]["wins"] == 1
    assert snap["you_book"]["losses"] == 1
    assert snap["together_book"]["n"] == 2
    assert snap["you_tickets"][0]["picked"] == "Hercog"
    line = make_take_line(snap["you_tickets"][0])
    assert "YOU $25 Hercog @2.0" in line
    assert "pinnacle" in line
    page = render_html(snap)
    assert "your book" in page
    assert "together (bot unique + you)" in page
    assert "Hercog" in page
    text = render_text(snap)
    assert "your book (logged tickets)" in text
    assert "together (bot unique + you)" in text
