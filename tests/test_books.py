from sxbot.books import bettor_card, format_books, format_card_oneline


def _ticket(
    *,
    result: str,
    decimal: float,
    stake: float = 10,
    pnl: float | None = None,
    ts: float = 1,
    league: str = "WTA",
    picked: str = "A",
    clv_pct: float | None = None,
) -> dict:
    if pnl is None and result == "win":
        pnl = stake * (decimal - 1)
    if pnl is None and result == "lose":
        pnl = -stake
    if pnl is None and result == "void":
        pnl = 0.0
    row = {
        "result": result,
        "decimal": decimal,
        "stake_usdc": stake,
        "pnl_usdc": pnl,
        "ts": ts,
        "league": league,
        "picked": picked,
        "when": "2026-09-01 12:00 UTC",
        "label": f"{picked} / B",
        "book": "sx",
    }
    if clv_pct is not None:
        row["clv_pct"] = clv_pct
    return row


def test_bettor_card_core_metrics() -> None:
    views = [
        _ticket(result="win", decimal=1.50, stake=10, ts=1, clv_pct=2.0),
        _ticket(result="lose", decimal=1.80, stake=10, ts=2, clv_pct=-1.0),
        _ticket(result="pending", decimal=1.40, stake=25, pnl=None, ts=3),
    ]
    card = bettor_card(views)
    assert card["wins"] == 1
    assert card["losses"] == 1
    assert card["win_pct"] == 50.0
    assert card["stake_usdc"] == 20.0
    assert card["pnl_usdc"] == -5.0  # +5 win, -10 lose
    assert card["roi_pct"] == -25.0
    assert card["units"] == -0.5
    assert card["pending"] == 1
    assert card["open_usdc"] == 25.0
    assert card["avg_decimal"] == 1.65
    assert card["breakeven_win_pct"] == round(100.0 / 1.65, 1)
    assert card["streak"] == "L1"
    assert card["max_drawdown_usdc"] == 10.0
    assert card["clv"]["n"] == 2
    assert card["clv"]["beat_close"] == 1
    assert "WTA" in card["by_league"]


def test_voids_do_not_count_in_win_pct() -> None:
    views = [
        _ticket(result="win", decimal=2.0, stake=5, ts=1),
        _ticket(result="void", decimal=2.0, stake=5, ts=2),
        _ticket(result="lose", decimal=2.0, stake=5, ts=3),
    ]
    card = bettor_card(views)
    assert card["wins"] == 1
    assert card["losses"] == 1
    assert card["voids"] == 1
    assert card["win_pct"] == 50.0
    assert card["stake_usdc"] == 10.0


def test_format_books_keeps_three_piles() -> None:
    bot = bettor_card([_ticket(result="win", decimal=1.5, stake=5, picked="BotPick")])
    you = bettor_card([_ticket(result="lose", decimal=2.0, stake=25, picked="YouPick")])
    together = bettor_card(
        [
            _ticket(result="win", decimal=1.5, stake=5, picked="BotPick"),
            _ticket(result="lose", decimal=2.0, stake=25, picked="YouPick"),
        ]
    )
    text = format_books(bot, you, together, you_tickets=[
        _ticket(result="lose", decimal=2.0, stake=25, picked="YouPick")
    ])
    assert "BOT  sxbot run unique" in text
    assert "YOU  tickets you logged" in text
    assert "TOGETHER" in text
    assert "YouPick" in text
    assert "paper assumes fills" in text
    assert format_card_oneline(bot).startswith("1–0")
