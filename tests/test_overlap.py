from sxbot.overlap import (
    attribute_quotes,
    attribute_tape,
    format_overlap_report,
    format_tag,
    tag_signal,
)
from sxbot.wallets import KNOWN_WALLETS
from tests.test_v2 import _order


HEDGE = KNOWN_WALLETS["HedgeHog"]
GARY = KNOWN_WALLETS["GambleGuruGary"]
LABELED = {HEDGE: "HedgeHog", GARY: "GambleGuruGary"}


def test_attribute_quotes_splits_sides_and_share() -> None:
    orders = [
        _order(maker=HEDGE, totalBetSize="8000000", isMakerBettingOutcomeOne=True),
        _order(
            maker="0x1111111111111111111111111111111111111111",
            totalBetSize="2000000",
            isMakerBettingOutcomeOne=True,
            orderHash="0x2",
        ),
        _order(
            maker=GARY,
            totalBetSize="1000000",
            isMakerBettingOutcomeOne=False,
            orderHash="0x3",
        ),
    ]
    by_market = attribute_quotes(orders, LABELED)
    quotes = by_market["0xabc"]
    assert quotes.on_one == ("HedgeHog",)
    assert quotes.on_two == ("GambleGuruGary",)
    assert quotes.share_on_side("outcome_one") == 0.8
    tag = tag_signal(quotes, side="outcome_one", takers=())
    assert tag["quoted_by"] == ["HedgeHog"]
    assert tag["quoted_against"] == ["GambleGuruGary"]
    assert tag["overlap"] is True
    assert "HedgeHog" in format_tag(tag)


def test_unknown_makers_are_overlap_none() -> None:
    orders = [_order(maker="0x1111111111111111111111111111111111111111")]
    quotes = attribute_quotes(orders, LABELED)["0xabc"]
    tag = tag_signal(quotes, side="outcome_one")
    assert tag["quoted_by"] == []
    assert tag["overlap"] is False
    assert format_tag(tag) == "overlap=none"


def test_attribute_tape_keeps_takers_not_makers() -> None:
    trades = [
        {
            "marketHash": "0xAbC",
            "maker": False,
            "bettor": GARY,
            "bettingOutcomeOne": True,
        },
        {
            "marketHash": "0xabc",
            "maker": True,
            "bettor": HEDGE,
            "bettingOutcomeOne": True,
        },
    ]
    tape = attribute_tape(trades, LABELED)
    assert tape["0xabc"]["outcome_one"] == ("GambleGuruGary",)
    tag = tag_signal(None, side="outcome_one", takers=tape["0xabc"]["outcome_one"])
    assert tag["takers"] == ["GambleGuruGary"]
    assert tag["overlap"] is True


def test_format_overlap_report_counts_by_motive() -> None:
    rows = [
        {
            "motive": "maker_steam",
            "actionable": True,
            "quoted_by": ["HedgeHog"],
            "quoted_against": [],
            "takers": [],
            "overlap": True,
            "overlap_any": True,
        },
        {
            "motive": "tob_lag",
            "actionable": True,
            "quoted_by": [],
            "quoted_against": [],
            "takers": [],
            "overlap": False,
            "overlap_any": False,
        },
    ]
    text = format_overlap_report(rows)
    assert "maker_steam" in text
    assert "tob_lag" in text
    assert "HedgeHog" in text
    assert "1/2" in text


def test_untagged_log_explains_v2() -> None:
    text = format_overlap_report([{"motive": "tob_lag"}])
    assert "no overlap tags" in text
    assert "No flow logged" in format_overlap_report([])
