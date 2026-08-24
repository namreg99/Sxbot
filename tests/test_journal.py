from sxbot.journal import format_summary, load_jsonl


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


def test_summary_includes_style_paper_logs(tmp_path) -> None:
    flow = tmp_path / "flow.jsonl"
    paper = tmp_path / "sxbot-paper.jsonl"
    styled = tmp_path / "sxbot-paper-mlb.jsonl"
    flow.write_text("", encoding="utf-8")
    paper.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"old","odds_pct":52.0,"stake_usdc":5,"ts":1}\n',
        encoding="utf-8",
    )
    styled.write_text(
        '{"action":"join_maker","phase":"pregame","side":"outcome_one","label":"mlb","odds_pct":51.0,"stake_usdc":5,"ts":2}\n',
        encoding="utf-8",
    )
    text = format_summary(flow, paper)
    assert "sxbot-paper.jsonl" in text
    assert "sxbot-paper-mlb.jsonl" in text
    assert "intended stake  10.0 USDC" in text
