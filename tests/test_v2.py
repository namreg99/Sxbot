from sxbot.orderbook import analyze
from sxbot.v2 import book_from_v2_orders, books_from_v2_orders, public_trades_from_v2, remaining_size


def _order(**overrides):
    row = {
        "marketHash": "0xabc",
        "fillAmount": "0",
        "pendingFillAmount": "0",
        "orderHash": "0x1",
        "maker": "0xdead",
        "totalBetSize": "10000000",
        "percentageOdds": "50000000000000000000",
        "isMakerBettingOutcomeOne": True,
        "orderStatus": "ACTIVE",
        "apiExpiry": 9_999_999_999,
    }
    row.update(overrides)
    return row


def test_remaining_size_subtracts_fill_and_pending() -> None:
    assert remaining_size(_order(totalBetSize="10000000", fillAmount="3000000", pendingFillAmount="1000000")) == 6_000_000


def test_aggregates_same_price_and_ignores_addresses() -> None:
    orders = [
        _order(percentageOdds="50000000000000000000", totalBetSize="5000000", maker="0xaaa"),
        _order(percentageOdds="50000000000000000000", totalBetSize="7000000", maker="0xbbb"),
        _order(
            percentageOdds="48000000000000000000",
            totalBetSize="3000000",
            isMakerBettingOutcomeOne=False,
        ),
    ]
    book = book_from_v2_orders("0xabc", orders, version="1")
    view = analyze(book)
    assert view.size_one == 12_000_000
    assert view.size_two == 3_000_000
    assert view.best_one == 50_000_000_000_000_000_000
    assert book.outcome_one[0].size == 12_000_000


def test_skips_filled_and_expired() -> None:
    orders = [
        _order(fillAmount="10000000"),
        _order(orderStatus="INACTIVE", orderHash="0x2"),
        _order(apiExpiry=1, orderHash="0x3"),
        _order(orderHash="0x4", totalBetSize="2000000"),
    ]
    book = book_from_v2_orders("0xabc", orders, version="1", now=100)
    assert len(book.outcome_one) == 1
    assert book.outcome_one[0].size == 2_000_000


def test_books_from_flat_list_group_by_hash() -> None:
    orders = [
        _order(marketHash="0xa", totalBetSize="1000000"),
        _order(marketHash="0xb", totalBetSize="2000000", isMakerBettingOutcomeOne=False),
    ]
    books = books_from_v2_orders(orders, version="9", market_hashes=["0xa", "0xb", "0xc"])
    assert books["0xa"].outcome_one[0].size == 1_000_000
    assert books["0xb"].outcome_two[0].size == 2_000_000
    assert books["0xc"].outcome_one == ()


def test_taker_row_keeps_taker_outcome() -> None:
    trades = public_trades_from_v2(
        [
            {
                "fillHash": "0xf",
                "marketHash": "0xabc",
                "maker": False,
                "bettingOutcomeOne": True,
                "stake": "5000000",
                "odds": "51000000000000000000",
                "createdAt": "2026-08-21T00:00:00Z",
            }
        ]
    )
    assert len(trades) == 1
    assert trades[0].is_betting_outcome_one is True
    assert trades[0].stake == 5_000_000


def test_maker_row_flips_to_taker_outcome_and_prefers_taker_duplicate() -> None:
    trades = public_trades_from_v2(
        [
            {
                "fillHash": "0xf",
                "marketHash": "0xabc",
                "maker": True,
                "bettingOutcomeOne": True,
                "stake": "1000000",
                "odds": "50000000000000000000",
            },
            {
                "fillHash": "0xf",
                "marketHash": "0xabc",
                "maker": False,
                "bettingOutcomeOne": False,
                "stake": "2000000",
                "odds": "50000000000000000000",
            },
        ]
    )
    assert len(trades) == 1
    assert trades[0].is_betting_outcome_one is False
    assert trades[0].stake == 2_000_000


def test_maker_only_row_still_maps_to_taker_side() -> None:
    trades = public_trades_from_v2(
        [
            {
                "fillHash": "0xf",
                "marketHash": "0xabc",
                "maker": True,
                "bettingOutcomeOne": True,
                "stake": "1000000",
                "odds": "50000000000000000000",
            }
        ]
    )
    assert trades[0].is_betting_outcome_one is False
