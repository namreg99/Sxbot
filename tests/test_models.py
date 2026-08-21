from sxbot.api import SxClient
from sxbot.models import Book, ExchangeMeta, Market


def test_market_from_api() -> None:
    market = Market.from_api(
        {
            "marketHash": "0x1",
            "status": "ACTIVE",
            "type": 226,
            "sportId": 8,
            "sportLabel": "Football",
            "leagueId": 243,
            "leagueLabel": "NFL",
            "sportXeventId": "L1",
            "teamOneName": "A",
            "teamTwoName": "B",
            "outcomeOneName": "A ML",
            "outcomeTwoName": "B ML",
            "gameTime": 1,
            "liveEnabled": False,
            "mainLine": True,
        }
    )
    assert market.label == "A ML / B ML"
    assert market.league_id == 243


def test_book_from_api() -> None:
    book = Book.from_api(
        {
            "marketHash": "0x1",
            "version": "001",
            "outcomeOne": [{"percentageOdds": "50000000000000000000", "size": "1000000"}],
            "outcomeTwo": [],
        }
    )
    assert len(book.outcome_one) == 1
    assert book.outcome_two == ()


def test_metadata_from_api() -> None:
    meta = ExchangeMeta.from_api(
        {
            "chainId": 4162,
            "domain": {"name": "OBv3 Escrow", "version": "1", "chainId": 4162, "verifyingContract": "0xE"},
            "activeAsset": {
                "symbol": "USDC",
                "baseToken": "0xT",
                "escrowAddress": "0xE",
                "decimals": 6,
            },
            "oddsLadderStepSize": 125,
            "limits": {
                "orderSizeMinimumBaseUnits": "5000000",
                "minRestingOrderSizeBaseUnits": "100000",
                "maxCreateOrders": 10,
                "maxCancelOrders": 100,
            },
        }
    )
    assert meta.min_order == 5_000_000
    assert meta.odds_ladder_step_size == 125


def test_client_headers() -> None:
    client = SxClient("https://api.sx.bet", api_key="abc", user_agent="sxbot-test")
    try:
        assert client._http.headers["x-sx-api-key"] == "abc"
        assert client._http.headers["User-Agent"] == "sxbot-test"
    finally:
        client.close()
