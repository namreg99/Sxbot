from sxbot.replay import (
    dog_steam_size_reason,
    format_replay,
    is_underdog_ticket,
    model_keep_reason,
    split_dog_steam_size,
    split_model,
    with_take_haircut,
)


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
    assert "underdog take" in text
    assert "steam+size" in text


def test_underdog_steam_size_not_shorts() -> None:
    dog = {
        "style": "tennis_dog",
        "motive": "maker_steam",
        "side": "outcome_one",
        "imbalance": 0.4,
        "decimal": 2.8,
        "result": "win",
        "stake_usdc": 5,
        "pnl_usdc": 9,
        "odds_pct": 35.7,
        "action": "join_maker",
    }
    thin = {
        "style": "tennis_dog",
        "motive": "maker_steam",
        "side": "outcome_one",
        "imbalance": 0,
        "decimal": 2.8,
        "result": "lose",
        "stake_usdc": 5,
        "pnl_usdc": -5,
    }
    short = {
        "style": "tennis_short",
        "motive": "maker_steam",
        "side": "outcome_one",
        "imbalance": 0.4,
        "decimal": 1.3,
    }
    fade = {
        "style": "tennis_dog",
        "motive": "tob_lag",
        "side": "outcome_two",
        "imbalance": -0.5,
        "reason": "depth-weighted; fade non-EV short",
        "decimal": 2.7,
    }
    assert is_underdog_ticket(dog)
    assert not is_underdog_ticket(short)
    assert dog_steam_size_reason(dog) == "steam+size"
    assert dog_steam_size_reason(thin) is None
    assert dog_steam_size_reason(fade) == "fade to dog"
    dogs, hit, miss = split_dog_steam_size([dog, thin, short, fade])
    assert dogs == [dog, thin, fade]
    assert hit == [dog, fade]
    assert miss == [thin]


def test_take_haircut_shortens_join_wins() -> None:
    join_win = {
        "action": "join_maker",
        "result": "win",
        "stake_usdc": 5,
        "pnl_usdc": 9.0,
        "odds_pct": 35.7,
    }
    take_win = {
        "action": "take_flow",
        "result": "win",
        "stake_usdc": 5,
        "pnl_usdc": 8.0,
        "odds_pct": 35.7,
    }
    lose = {
        "action": "join_maker",
        "result": "lose",
        "stake_usdc": 5,
        "pnl_usdc": -5,
        "odds_pct": 35.7,
    }
    out = with_take_haircut([join_win, take_win, lose])
    assert out[0]["pnl_usdc"] < 9.0
    assert out[1]["pnl_usdc"] == 8.0
    assert out[2]["pnl_usdc"] == -5.0
