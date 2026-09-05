from sxbot.fills import live_entry_filled, live_filled_base_units, order_ids


def test_rested_offer_is_not_a_live_fill() -> None:
    result = {
        "orders": [
            {
                "orderId": "0xabc",
                "status": "SUBMITTED",
                "outcome": {"state": "RESTED", "remainingAmount": "1000000"},
            }
        ]
    }
    assert live_filled_base_units(result, 1_000_000) == 0
    assert live_entry_filled(result, 1_000_000) is False
    assert order_ids(result) == ["0xabc"]


def test_fully_filled_is_a_live_fill() -> None:
    result = {
        "orders": [
            {
                "orderId": "0xdef",
                "status": "SUBMITTED",
                "outcome": {"state": "FULLY_FILLED", "remainingAmount": "0"},
            }
        ]
    }
    assert live_entry_filled(result, 1_000_000) is True


def test_partial_remaining_counts_as_entry() -> None:
    result = {
        "orders": [
            {
                "orderId": "0x1",
                "outcome": {"state": "RESTED", "remainingAmount": "400000"},
            }
        ]
    }
    assert live_filled_base_units(result, 1_000_000) == 600_000
    assert live_entry_filled(result, 1_000_000) is True
