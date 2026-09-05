from datetime import datetime, timedelta, timezone

from sxbot.rollout import MAINNET_API, TESTNET_API, V3_MAINNET_LIVE_AT, uses_v2_books, v3_mainnet_is_live


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


def test_mainnet_uses_v2_before_cutover() -> None:
    before = V3_MAINNET_LIVE_AT - timedelta(seconds=1)
    assert uses_v2_books(MAINNET_API, now=before)
    assert not uses_v2_books(MAINNET_API, now=V3_MAINNET_LIVE_AT)
    assert not uses_v2_books(TESTNET_API, now=before)


def test_book_source_override() -> None:
    assert uses_v2_books(TESTNET_API, book_source="v2")
    assert not uses_v2_books(MAINNET_API, now=V3_MAINNET_LIVE_AT - timedelta(days=1), book_source="v3")
