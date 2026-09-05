from pathlib import Path

from sxbot.learn import (
    MakerCell,
    MakerModel,
    cell_key,
    fit_maker_model,
    format_maker_model,
    key_for_quote,
)
from sxbot.mm import quote_pair
from sxbot.orderbook import analyze
from sxbot.units import OddsLadder, from_percent
from tests.conftest import make_book, make_settings
from tests.test_mm import _mlb, _soccer


def test_maker_model_shrinks_tiny_hot_streak_toward_zero() -> None:
    hot = MakerCell(n=40, stake=8_000, pnl=4_000)
    model = MakerModel(cells={"x": hot}, prior_stake=25_000, prior_roi=0.0)
    # Raw ROI is +50%, but stake is below MIN_CELL_STAKE so score is None.
    assert hot.raw_roi() == 0.5
    assert model.roi("x") is None
    fat = MakerCell(n=200, stake=80_000, pnl=16_000)
    model.cells["y"] = fat
    shrunk = model.roi("y")
    assert shrunk is not None
    assert 0 < shrunk < fat.raw_roi()


def test_maker_model_rejects_red_cell() -> None:
    red = MakerCell(n=400, stake=100_000, pnl=-20_000)
    model = MakerModel(cells={"Soccer|type-1|pick 1.80–2.20|pregame": red}, prior_stake=25_000)
    market = _soccer()
    price = from_percent(55.0)
    assert key_for_quote(market, price) == "Soccer|type-1|pick 1.80–2.20|pregame"
    assert model.allow(market, price, 0.0) is None


def test_quote_pair_skips_when_model_says_red() -> None:
    # 32% soccer dog is in the hardcoded band; a red fitted cell still blocks it.
    dog = analyze(make_book(o1=((32.0, 40),), o2=((67.0, 5),)))
    key = cell_key("Soccer", "type-1", "dog 2.20–3.50", "pregame")
    model = MakerModel(cells={key: MakerCell(n=200, stake=80_000, pnl=-12_000)}, prior_stake=25_000)
    assert quote_pair(_soccer(), dog, OddsLadder(125), make_settings(), model=model) is None
    green = MakerModel(cells={key: MakerCell(n=200, stake=80_000, pnl=16_000)}, prior_stake=25_000)
    pair = quote_pair(_soccer(), dog, OddsLadder(125), make_settings(), model=green)
    assert pair is not None
    assert pair.roi is not None and pair.roi > 0
    assert "roi" in pair.reason


def test_quote_pair_without_model_still_uses_hardcoded_bands() -> None:
    view = analyze(make_book(o1=((62.0, 40),), o2=((37.0, 5),)))
    pair = quote_pair(_mlb(), view, OddsLadder(125), make_settings())
    assert pair is not None
    assert pair.roi == 0.0


def test_fit_and_roundtrip(tmp_path: Path) -> None:
    from sxbot.archive import HistoryStore

    sqlite = tmp_path / "h.sqlite"
    with HistoryStore(sqlite) as store:
        store.upsert_wallet("0x" + "a" * 40, "TestMaker")
        # empty fills → empty model still saves
        model = fit_maker_model(store, prior_stake=10_000)
    path = tmp_path / "m.json"
    model.save(path)
    loaded = MakerModel.load(path)
    assert loaded.prior_stake == 10_000
    text = format_maker_model(loaded)
    assert "Not a neural net" in text
    assert loaded.cells == {}


def test_fit_from_archive_marks_soccer_pickem_red() -> None:
    from pathlib import Path

    sqlite = Path("sxbot-history.sqlite")
    if not sqlite.exists():
        return
    from sxbot.archive import HistoryStore

    with HistoryStore(sqlite) as store:
        model = fit_maker_model(store)
    key = cell_key("Soccer", "type-1", "pick 1.80–2.20", "pregame")
    dog = cell_key("Soccer", "type-1", "dog 2.20–3.50", "pregame")
    assert model.roi(key) is not None and model.roi(key) < 0
    assert model.roi(dog) is not None and model.roi(dog) > 0
