from sxbot.models import Action, ExchangeMeta, Side, Signal
from sxbot.executor import Executor
from tests.conftest import make_market, make_settings


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
    record = executor.execute(signal, 5_000_000)
    assert record["dry_run"] is True
    assert paper.exists()
    line = paper.read_text(encoding="utf-8").strip()
    assert "join_maker" in line
    assert "Rams" in line
