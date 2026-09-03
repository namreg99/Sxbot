from sxbot.journal import (
    format_summary,
    iter_paper_logs,
    live_log_for,
    load_follow_live,
    load_follow_paper,
    load_jsonl,
)


def test_summary_empty(tmp_path) -> None:
    text = format_summary(tmp_path / "flow.jsonl", tmp_path / "paper.jsonl")
    assert "No logs yet" in text


def test_summary_counts_motive_and_phase(tmp_path) -> None:
    flow = tmp_path / "flow.jsonl"
    paper = tmp_path / "paper.jsonl"
    flow.write_text(
        '{"motive":"maker_steam","phase":"pregame","actionable":true,"label":"A / B","league":"EPL","side":"outcome_one"}\n'
        '{"motive":"taker_hit","phase":"live","actionable":false,"label":"C / D","league":"MLB","side":"outcome_two"}\n',
        encoding="utf-8",
    )
    paper.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"A / B","odds_pct":52.0,"stake_usdc":5}\n',
        encoding="utf-8",
    )
    text = format_summary(flow, paper)
    assert "maker_steam" in text
    assert "pregame" in text
    assert "join_maker" in text
    assert load_jsonl(flow)[0]["motive"] == "maker_steam"


def test_all_paper_logs_load_nothing_is_orphaned(tmp_path) -> None:
    flow = tmp_path / "flow.jsonl"
    paper = tmp_path / "sxbot-paper.jsonl"
    styled = tmp_path / "sxbot-paper-mlb.jsonl"
    recovered = tmp_path / "sxbot-paper-recovered.jsonl"
    flow.write_text("", encoding="utf-8")
    paper.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"old","odds_pct":52.0,"stake_usdc":5,"ts":1}\n',
        encoding="utf-8",
    )
    styled.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"mlb","odds_pct":51.0,"stake_usdc":5,"ts":2}\n',
        encoding="utf-8",
    )
    recovered.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"rec","odds_pct":50.0,"stake_usdc":5,"ts":3,"style":"recovered"}\n',
        encoding="utf-8",
    )
    text = format_summary(flow, paper)
    # Legacy dump and recovered history load alongside style files — history
    # in an old file must never silently drop out of the books again.
    assert "sxbot-paper.jsonl" in text
    assert "sxbot-paper-mlb.jsonl" in text
    assert "sxbot-paper-recovered.jsonl" in text
    assert [p.name for p in iter_paper_logs(paper)] == [
        "sxbot-paper.jsonl",
        "sxbot-paper-mlb.jsonl",
        "sxbot-paper-recovered.jsonl",
    ]


def test_follow_paper_drops_mm_quotes(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    mlb = tmp_path / "sxbot-paper-mlb.jsonl"
    mm = tmp_path / "sxbot-paper-mm.jsonl"
    mlb.write_text(
        '{"action":"join_maker","style":"mlb","market":"0x1","side":"outcome_one","ts":1}\n',
        encoding="utf-8",
    )
    mm.write_text(
        '{"action":"join_maker","style":"mm","market":"0x2","side":"outcome_one","ts":2}\n',
        encoding="utf-8",
    )
    rows = load_follow_paper(paper)
    assert len(rows) == 1
    assert rows[0]["style"] == "mlb"


def test_live_log_stays_off_the_paper_card(tmp_path) -> None:
    paper = tmp_path / "sxbot-paper.jsonl"
    mlb = tmp_path / "sxbot-paper-mlb.jsonl"
    live = tmp_path / "sxbot-live-mlb.jsonl"
    mlb.write_text(
        '{"action":"join_maker","style":"mlb","market":"0x1","side":"outcome_one","ts":1}\n',
        encoding="utf-8",
    )
    live.write_text(
        '{"action":"join_maker","style":"mlb","market":"0x9","side":"outcome_one","ts":2}\n',
        encoding="utf-8",
    )
    assert live_log_for(paper, "mlb") == live
    assert [r["market"] for r in load_follow_paper(paper)] == ["0x1"]
    assert [r["market"] for r in load_follow_live(paper)] == ["0x9"]
