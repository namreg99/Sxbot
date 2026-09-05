import pytest

from sxbot.models import Action, ExchangeMeta, Side, Signal
from sxbot.executor import Executor, _bytes32, normalize_private_key
from sxbot.units import from_percent
from tests.conftest import make_market, make_settings


def test_normalize_private_key_strips_quotes_and_0x() -> None:
    raw = "11" * 32
    assert normalize_private_key(raw) == "0x" + raw
    assert normalize_private_key("  0x" + raw + "  ") == "0x" + raw
    assert normalize_private_key('"' + raw + '"') == "0x" + raw


def test_bytes32_pads_short_hashes() -> None:
    assert _bytes32("0xabc") == "0x" + "0" * 61 + "abc"
    assert _bytes32("0x" + "ab" * 32) == "0x" + "ab" * 32
    with pytest.raises(ValueError, match="not hexadecimal"):
        normalize_private_key("not-a-private-key-value-at-all-xxxx")
    with pytest.raises(ValueError, match="64 hex"):
        normalize_private_key("abcd")


def test_dry_run_writes_paper_log(tmp_path) -> None:
    meta = ExchangeMeta(
        chain_id=4162,
        domain={},
        base_token="0x0",
        decimals=6,
        escrow="0x0",
        odds_ladder_step_size=125,
        min_order=5_000_000,
        min_resting=100_000,
        max_create=10,
        max_cancel=100,
    )
    paper = tmp_path / "paper.jsonl"
    executor = Executor(make_settings(paper_log=str(paper)), meta, client=None, paper_path=paper)
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=52_875_000_000_000_000_000,
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
    )
    record = executor.execute(signal, 5_000_000, extra={"source": "mimic", "copied_wallet": "GambleGuruGary"})
    assert record["dry_run"] is True
    assert record["source"] == "mimic"
    assert paper.exists()
    line = paper.read_text(encoding="utf-8").strip()
    assert "join_maker" in line
    assert "Rams" in line
    assert "GambleGuruGary" in line
    assert record["game_time"] == signal.market.game_time
    assert record["event_id"] == "L1"
    assert record["style"] == ""


def test_dry_run_stamps_flat_and_kelly_books(tmp_path) -> None:
    paper = tmp_path / "paper.jsonl"
    executor = Executor(make_settings(paper_log=str(paper)), _meta(), client=None, paper_path=paper)
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
        style="mlb",
    )
    record = executor.execute(signal, 5_000_000)
    assert record["flat_stake_usdc"] == 5.0
    assert record["kelly_stake_usdc"] == 25.0
    assert record["fair_pct"] == 55.0
    assert record["stake_usdc"] == 5.0
    skip = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="no edge",
        mid_move_bps=10,
        imbalance=0.1,
        confidence=0.5,
        fair_odds=from_percent(50.0),
    )
    skipped = executor.execute(skip, 5_000_000)
    assert skipped["kelly_stake_usdc"] is None
    assert skipped["flat_stake_usdc"] == 5.0


def test_tracker_stamps_ignore_live_dollar_caps(tmp_path) -> None:
    paper = tmp_path / "paper.jsonl"
    settings = make_settings(
        paper_log=str(paper),
        stake_usdc=1,
        max_per_market_usdc=4,
        bankroll_usdc=145,
    )
    executor = Executor(settings, _meta(), client=None, paper_path=paper)
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(50.0),
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
        fair_odds=from_percent(55.0),
        style="mlb",
    )
    record = executor.execute(signal, 1_000_000)
    assert record["stake_usdc"] == 1.0
    assert record["flat_stake_usdc"] == 5.0
    assert record["kelly_stake_usdc"] == 25.0
    assert record["bankroll_usdc"] == 1000.0


def _meta() -> ExchangeMeta:
    return ExchangeMeta(
        chain_id=4162,
        domain={},
        base_token="0x0",
        decimals=6,
        escrow="0x0",
        odds_ladder_step_size=125,
        min_order=5_000_000,
        min_resting=100_000,
        max_create=10,
        max_cancel=100,
    )


def test_dry_run_writes_style_paper_log(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    executor = Executor(make_settings(paper_log=str(paper)), _meta(), client=None)
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=52_875_000_000_000_000_000,
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
        style="mlb",
    )
    executor.execute(signal, 5_000_000)
    styled = tmp_path / "sxbot-paper-mlb.jsonl"
    assert styled.exists()
    assert not paper.exists()
    line = styled.read_text(encoding="utf-8")
    assert '"style": "mlb"' in line
    assert '"event_id": "L1"' in line


def test_unstyled_write_does_not_recreate_main_dump(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    executor = Executor(make_settings(paper_log=str(paper)), _meta(), client=None)
    signal = Signal(
        market=make_market(),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=52_875_000_000_000_000_000,
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.2,
        confidence=0.8,
    )
    executor.execute(signal, 5_000_000)
    assert not paper.exists()
    legacy = tmp_path / "sxbot-paper-legacy.jsonl"
    assert legacy.exists()
    assert "join_maker" in legacy.read_text(encoding="utf-8")


def test_live_totals_write_paper_not_live_log(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    settings = make_settings(paper_log=str(paper), dry_run=False)
    executor = Executor(settings, _meta(), client=None)
    signal = Signal(
        market=make_market(
            type=28,
            outcome_one="Over 8.5",
            outcome_two="Under 8.5",
        ),
        side=Side.OUTCOME_ONE,
        action=Action.JOIN_MAKER,
        maker_odds=from_percent(52.0),
        reason="makers shifted",
        mid_move_bps=80,
        imbalance=0.4,
        confidence=0.8,
        style="totals",
    )
    record = executor.execute(signal, 5_000_000)
    assert record["track_only"] is True
    assert record["live_filled"] is False
    tracked = tmp_path / "sxbot-paper-totals.jsonl"
    assert tracked.exists()
    assert not (tmp_path / "sxbot-live-totals.jsonl").exists()
    assert '"style": "totals"' in tracked.read_text(encoding="utf-8")
