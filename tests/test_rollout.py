from datetime import datetime, timedelta, timezone

from sxbot.rollout import V3_MAINNET_LIVE_AT, v3_mainnet_is_live


def test_not_live_before_cutover() -> None:
    assert not v3_mainnet_is_live(V3_MAINNET_LIVE_AT - timedelta(seconds=1))


def test_live_at_and_after_cutover() -> None:
    assert v3_mainnet_is_live(V3_MAINNET_LIVE_AT)
    assert v3_mainnet_is_live(V3_MAINNET_LIVE_AT + timedelta(days=1))


def test_defaults_to_now_when_unset() -> None:
    # Sanity: calling with no argument does not raise and returns a bool.
    assert isinstance(v3_mainnet_is_live(), bool)


def test_cutover_is_the_documented_date() -> None:
    assert V3_MAINNET_LIVE_AT == datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)
