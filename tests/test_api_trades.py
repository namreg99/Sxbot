from sxbot.api import as_market_rows, index_markets, lookup_market, v2_trade_query_params


def test_trades_use_pagination_key_not_next_key() -> None:
    params = v2_trade_query_params(
        "0xabc",
        as_maker=False,
        per_page=100,
        start_date=1764547200,
        end_date=1770681600,
        pagination_key="692d75f1",
    )
    assert params["bettor"] == "0xabc"
    assert params["maker"] == "false"
    assert params["startDate"] == 1764547200
    assert params["endDate"] == 1770681600
    assert params["paginationKey"] == "692d75f1"
    assert "nextKey" not in params


def test_maker_flag_and_omitted_cursor() -> None:
    params = v2_trade_query_params("0xabc", as_maker=True)
    assert params["maker"] == "true"
    assert "paginationKey" not in params
    assert "startDate" not in params


def test_as_market_rows_unwraps_list_and_nested_payloads() -> None:
    row = {"marketHash": "0xAbC", "outcome": 1}
    assert as_market_rows([row]) == [row]
    assert as_market_rows({"markets": [row]}) == [row]
    assert as_market_rows({"data": [row]}) == [row]
    assert as_market_rows(row) == [row]
    assert as_market_rows({"status": "success", "data": {"markets": [row]}}) == [row]


def test_index_and_lookup_are_case_insensitive() -> None:
    indexed = index_markets([{"marketHash": "0xAbC", "outcome": 1}])
    assert lookup_market(indexed, "0xabc")["outcome"] == 1
    assert lookup_market({"0xAbC": {"outcome": 2}}, "0xabc")["outcome"] == 2
