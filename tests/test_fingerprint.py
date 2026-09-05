from sxbot.fingerprint import (
    format_profiles,
    profile_wallet,
    role_from_counts,
    suggested_style,
    trade_pnl_usdc,
)


def test_role_thresholds() -> None:
    assert role_from_counts(80, 20) == "maker"
    assert role_from_counts(20, 80) == "taker"
    assert role_from_counts(50, 50) == "mixed"
    assert suggested_style("taker") == "take"


def test_pnl_win_lose_void() -> None:
    assert trade_pnl_usdc({"settled": True, "stake": "5000000", "settleNetReturnValue": 10.0}) == 5.0
    assert trade_pnl_usdc({"settled": True, "stake": "5000000", "settleNetReturnValue": 0}) == -5.0
    assert trade_pnl_usdc({"settled": True, "outcome": 0, "stake": "5000000", "settleNetReturnValue": 0}) == 0.0
    assert trade_pnl_usdc({"settled": False, "stake": "5000000"}) is None


def test_profile_maker_heavy() -> None:
    maker_fills = [
        {"settled": True, "stake": "5000000", "settleNetReturnValue": 10.0, "outcome": 1}
        for _ in range(8)
    ]
    taker_fills = [
        {"settled": True, "stake": "5000000", "settleNetReturnValue": 0, "outcome": 2}
        for _ in range(2)
    ]
    orders = [
        {
            "marketHash": "0xa",
            "isMakerBettingOutcomeOne": True,
            "totalBetSize": "10000000",
            "fillAmount": "0",
            "pendingFillAmount": "0",
            "orderStatus": "ACTIVE",
            "percentageOdds": "50000000000000000000",
        },
        {
            "marketHash": "0xa",
            "isMakerBettingOutcomeOne": False,
            "totalBetSize": "8000000",
            "fillAmount": "0",
            "pendingFillAmount": "0",
            "orderStatus": "ACTIVE",
            "percentageOdds": "48000000000000000000",
        },
    ]
    profile = profile_wallet(
        "0xabc",
        maker_fills=maker_fills,
        taker_fills=taker_fills,
        open_orders=orders,
        markets={"0xa": {"sportLabel": "Soccer"}},
    )
    assert profile.role == "maker"
    assert profile.style == "join"
    assert profile.two_sided_markets == 1
    assert "Soccer" in profile.sports
    text = format_profiles([profile])
    assert "SX_FOLLOW_STYLE=join" in text


def test_empty_profiles_explain_env() -> None:
    assert "SX_SHARP_WALLETS" in format_profiles([])
