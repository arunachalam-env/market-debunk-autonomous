"""Hashtag counts are enforced, not just requested in field descriptions."""
from src.agents.distribution_seo_agent import (
    YouTubeDistribution, InstagramDistribution, FacebookDistribution,
)


def test_youtube_hashtags_capped_at_5():
    yt = YouTubeDistribution(
        title="t", snippet="s", takeaways=["a", "b", "c"],
        hashtags=["#1", "#2", "#3", "#4", "#5", "#6", "#7", "#8"],
        search_tags=["x"], pinned_comment="p",
    )
    assert len(yt.hashtags) == 5


def test_instagram_hashtags_capped_at_4():
    ig = InstagramDistribution(
        first_line_hook="h", body_copy="b", share_save_cta="s",
        comment_trigger="c",
        hashtags=["#1", "#2", "#3", "#4", "#5", "#6", "#7", "#8"],
    )
    assert ig.hashtags == ["#1", "#2", "#3", "#4"]


def test_facebook_tags_capped_at_4():
    fb = FacebookDistribution(
        story_hook="h", narrative_body="b", discussion_question="q",
        topic_tags=["#1", "#2", "#3", "#4", "#5"],
    )
    assert len(fb.topic_tags) == 4


def test_under_cap_untouched():
    ig = InstagramDistribution(
        first_line_hook="h", body_copy="b", share_save_cta="s",
        comment_trigger="c", hashtags=["#1", "#2", "#3"],
    )
    assert ig.hashtags == ["#1", "#2", "#3"]
