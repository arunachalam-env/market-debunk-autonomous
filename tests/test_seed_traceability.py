"""Tests for the deterministic story-seed figure validator."""

from src.agents.topic_agent import _seed_figure_violations


def _seed(thesis, **fields):
    return {"thesis": thesis, "story_seed": fields}


def test_figures_present_in_source_pass():
    src = "HDFC AMC raised its expense ratio from 1.05% to 1.35% this quarter."
    result = _seed("Expense ratios jumped to 1.35% this quarter.", real_world_anchor="raised from 1.05% to 1.35%")
    assert _seed_figure_violations(result, src) == []


def test_invented_figures_flagged():
    src = "Companies are passing raw material costs to consumers amid inflation."
    result = _seed(
        "Companies hiked prices by up to 13% while margins grew.",
        concept_one_liner="A 7-9% hike in Q1 and 3-4% in Q2.",
    )
    bad = _seed_figure_violations(result, src)
    assert any("13" in b for b in bad)
    assert any("7-9" in b or "7" in b for b in bad)


def test_comma_normalization():
    src = "Investors lost Rs 15,00,000 over 25 years."
    result = _seed("You could lose ₹15,00,000 in fees.")
    assert _seed_figure_violations(result, src) == []
