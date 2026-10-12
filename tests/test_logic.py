import pytest

from app import metrics
from app.themes import RuleTagger, THEMES, parse_reply


def test_nps_basic():
    # 2 promoters, 1 passive, 1 detractor -> (2-1)/4 = +25
    r = metrics.nps([10, 9, 8, 3])
    assert r["nps"] == 25.0 and r["n"] == 4
    assert r["ci_low"] < 25 < r["ci_high"]


def test_nps_bounds_and_empty():
    assert metrics.nps([])["nps"] is None
    assert metrics.nps([10] * 50)["nps"] == 100.0
    assert metrics.nps([0] * 50)["nps"] == -100.0
    assert metrics.nps([0] * 50)["ci_low"] >= -100


def test_weekly_groups_by_iso_week():
    rows = [
        {"created_at": "2026-09-07 10:00:00", "score": 10},
        {"created_at": "2026-09-08 10:00:00", "score": 0},
        {"created_at": "2026-09-15 10:00:00", "score": 9},
    ]
    weeks = metrics.weekly_nps(rows)
    assert [w["n"] for w in weeks] == [2, 1]
    assert weeks[0]["nps"] == 0.0 and weeks[1]["nps"] == 100.0


def test_theme_breakdown_sorted_by_detractors():
    rows = [
        {"theme": "price_value", "score": 9},
        {"theme": "price_value", "score": 9},
        {"theme": "delivery", "score": 2},
        {"theme": "delivery", "score": 1},
        {"theme": None, "score": 1},
    ]
    out = metrics.theme_breakdown(rows)
    assert out[0]["theme"] == "delivery"
    assert out[0]["detractor_share"] == 100.0


@pytest.mark.parametrize("reply", [
    "not json",
    '{"theme": "weather", "sentiment": "negative"}',
    '{"theme": "delivery", "sentiment": "furious"}',
])
def test_parse_reply_rejects_out_of_taxonomy(reply):
    with pytest.raises(Exception):
        parse_reply(reply)


def test_parse_reply_accepts_wrapped_json():
    t = parse_reply('Sure! {"theme": "website_app", "sentiment": "neutral"}')
    assert t.theme == "website_app" and t.source == "claude"


def test_rule_tagger_always_returns_valid_labels():
    t = RuleTagger()
    for text in ["", "???", "late package", "LOVE THE POINTS"]:
        tag = t.tag(text, 5)
        assert tag.theme in THEMES + ("no_comment",) and tag.sentiment in ("positive", "neutral", "negative")


def test_rule_baseline_floor_on_labelled_set():
    """Guards against silent regressions in the fallback path."""
    import json
    from pathlib import Path

    rows = [json.loads(l) for l in (Path(__file__).parent / "fixtures" / "labeled_feedback.jsonl").read_text().splitlines()]
    t = RuleTagger()
    acc = sum(t.tag(r["comment"], r["score"]).theme == r["theme"] for r in rows) / len(rows)
    assert acc >= 0.5


def test_score_only_responses_excluded_from_theme_ranking():
    assert RuleTagger().tag("   ", 2).theme == "no_comment"
    rows = [{"theme": "no_comment", "score": 0}, {"theme": "delivery", "score": 9}]
    assert [t["theme"] for t in metrics.theme_breakdown(rows)] == ["delivery"]
