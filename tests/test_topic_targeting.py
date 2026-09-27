"""Tests for investor/stock content targeting in topic discovery pools."""
from src.agents import topic_agent


def test_market_evergreen_pool_includes_investor_topics():
    pool = topic_agent._EVERGREEN_MARKET_TOPICS + topic_agent._EVERGREEN_INVESTOR_TOPICS
    assert any("Buffett" in t for t in pool)
    assert any("Jhunjhunwala" in t for t in pool)
    assert any("Kedia" in t for t in pool)


def test_investor_topics_are_verifiable_case_studies():
    # Every investor topic should name a real person or real company/stock
    anchors = ("Buffett", "Munger", "Jhunjhunwala", "Kedia", "Damani", "Pabrai",
               "Lynch", "Yes Bank", "Paytm", "Asian Paints", "Suzlon", "DHFL",
               "IRCTC", "Eicher", "Titan")
    for topic in topic_agent._EVERGREEN_INVESTOR_TOPICS:
        assert any(a in topic for a in anchors), f"Topic lacks a named anchor: {topic}"


def test_no_duplicate_evergreen_topics():
    all_topics = (topic_agent._EVERGREEN_MARKET_TOPICS
                  + topic_agent._EVERGREEN_INVESTOR_TOPICS
                  + topic_agent._EVERGREEN_CONSUMER_TOPICS)
    assert len(all_topics) == len(set(all_topics))


def test_serp_investor_queries_exist():
    assert len(topic_agent._SERP_INVESTOR_QUERIES) >= 8
    joined = " ".join(topic_agent._SERP_INVESTOR_QUERIES)
    assert "Jhunjhunwala" in joined or "Buffett" in joined
