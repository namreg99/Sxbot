from sxbot.replay import format_replay, model_keep_reason, split_model


def test_keeps_maker_size_and_stale_and_fade() -> None:
    assert model_keep_reason({"motive": "maker_steam", "side": "outcome_one", "imbalance": 0.4})
    assert model_keep_reason({"motive": "take_stale", "side": "outcome_one", "imbalance": 0})
    assert model_keep_reason(
        {
            "motive": "tob_lag",
            "side": "outcome_two",
            "imbalance": -0.5,
            "reason": "depth-weighted; fade non-EV short",
        }
    )


def test_keeps_mlb_steam_without_size() -> None:
    assert model_keep_reason(
        {"style": "mlb", "motive": "maker_steam", "side": "outcome_one", "imbalance": 0}
    ) == "mlb steam"


def test_skips_tennis_short_even_with_size() -> None:
    assert (
        model_keep_reason(
            {
                "style": "tennis_short",
                "motive": "maker_steam",
                "side": "outcome_one",
                "imbalance": 0.4,
            }
        )
        is None
    )


def test_skips_soccer_steam_and_tob_lag_without_size() -> None:
    assert (
        model_keep_reason(
            {"style": "soccer", "motive": "maker_steam", "side": "outcome_one", "imbalance": 0}
        )
        is None
    )
    assert model_keep_reason({"motive": "tob_lag", "side": "outcome_one", "imbalance": -0.4}) is None


def test_split_and_format_replay() -> None:
    keep_row = {
        "style": "soccer",
        "motive": "maker_steam",
        "side": "outcome_one",
        "imbalance": 0.4,
        "result": "win",
        "stake_usdc": 5,
        "pnl_usdc": 1.5,
    }
    skip_row = {
        "style": "tennis_short",
        "motive": "maker_steam",
        "side": "outcome_one",
        "imbalance": 0.4,
        "result": "lose",
        "stake_usdc": 5,
        "pnl_usdc": -5,
    }
    keep, skip = split_model([keep_row, skip_row])
    assert keep == [keep_row]
    assert skip == [skip_row]
    text = format_replay([keep_row, skip_row], keep, skip)
    assert "keep model" in text
    assert "skip" in text
    assert "soccer" in text
    assert "tennis_short" in text
    assert "NOT a rewind" in text
    assert "MLB pick" in text
