from sxbot.api import v2_trade_query_params


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
