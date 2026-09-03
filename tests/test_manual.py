import json

from sxbot.manual import (
    build_ticket,
    find_ticket,
    load_manual,
    match_from_rows,
    name_hit,
    parse_odds,
    settle_ticket,
    side_for_picked,
    stamp_settled_view,
)
from sxbot.units import decimal_odds, to_prob


def test_parse_odds_decimal_percent_american() -> None:
    odds, pct, dec = parse_odds("1.78")
    assert abs(dec - 1.78) < 1e-6
    assert abs(pct - 100.0 / 1.78) < 0.05
    assert odds > 0
    _, _, dec_pct = parse_odds("56.25")
    assert abs(dec_pct - (100.0 / 56.25)) < 1e-6
    _, _, plus = parse_odds("+150")
    assert plus == 2.5
    _, _, minus = parse_odds("-110")
    assert abs(minus - (1.0 + 100.0 / 110.0)) < 1e-6
    assert decimal_odds(to_prob(odds)) > 1


def test_side_and_name_hit() -> None:
    assert name_hit("Krejcikova", "Barbora Krejcikova")
    assert side_for_picked("Krejcikova", "Kamilla Rakhimova", "Barbora Krejcikova") == "outcome_two"
    assert side_for_picked("Rakhimova", "Kamilla Rakhimova", "Barbora Krejcikova") == "outcome_one"
    assert side_for_picked("Nobody", "A", "B") is None


def test_match_from_paper_rows() -> None:
    rows = [
        {
            "market": "0xabc",
            "side": "outcome_two",
            "outcome_one": "Kamilla Rakhimova",
            "outcome_two": "Barbora Krejcikova",
            "label": "Kamilla Rakhimova / Barbora Krejcikova",
            "league": "WTA US Open",
        }
    ]
    hit = match_from_rows(rows, picked="Krejcikova")
    assert hit is not None
    assert hit["market"] == "0xabc"
    assert match_from_rows(rows, picked="Krejcikova", vs="Rakhimova")["market"] == "0xabc"
    assert match_from_rows(rows, picked="Djokovic") is None


def test_build_and_settle_roundtrip(tmp_path) -> None:
    path = tmp_path / "sxbot-manual.jsonl"
    ticket = build_ticket(
        picked="Barbora Krejcikova",
        odds_raw="1.45",
        stake_usdc=25,
        vs="Kamilla Rakhimova",
        book="pinnacle",
        now=100.0,
        resolved={
            "market": "0xabc",
            "side": "outcome_two",
            "outcome_one": "Kamilla Rakhimova",
            "outcome_two": "Barbora Krejcikova",
            "label": "Kamilla Rakhimova / Barbora Krejcikova",
            "league": "WTA US Open",
        },
    )
    assert ticket["action"] == "manual"
    assert ticket["style"] == "manual"
    assert ticket["side"] == "outcome_two"
    assert ticket["stake_usdc"] == 25
    assert ticket["decimal"] == 1.45
    assert ticket["book"] == "pinnacle"
    path.write_text(json.dumps(ticket) + "\n", encoding="utf-8")
    settle_ticket(path, ticket, "win")
    loaded = load_manual(path)
    assert len(loaded) == 1
    assert loaded[0]["settled_result"] == "win"
    view = stamp_settled_view(
        {"result": "pending", "stake_usdc": 25, "decimal": 1.45},
        loaded[0],
    )
    assert view["result"] == "win"
    assert view["pnl_usdc"] == 11.25
    found = find_ticket(loaded, picked="Krejcikova")
    assert found["ticket_id"] == ticket["ticket_id"]


def test_stamp_does_not_override_sx_grade() -> None:
    view = stamp_settled_view(
        {"result": "lose", "pnl_usdc": -25, "stake_usdc": 25, "decimal": 1.45},
        {"settled_result": "win"},
    )
    assert view["result"] == "lose"
    assert view["pnl_usdc"] == -25
