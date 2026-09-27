"""Tests for the first-2-seconds shock-anchor hook rules in the ScriptDoctor."""
from src.agents.rewriter_agent import EnglishScriptRewriterAgent


def _scenes(narration: str):
    return [{"scene_id": i + 1, "narration": narration if i == 0 else f"scene {i+1} body text here."} for i in range(6)]


def test_anchor_detector_numbers_and_names():
    assert EnglishScriptRewriterAgent._has_shock_anchor("Is your SIP losing ₹4,00,000 quietly?")
    assert EnglishScriptRewriterAgent._has_shock_anchor("Did Buffett really say that about your stocks?")
    assert EnglishScriptRewriterAgent._has_shock_anchor("Is SEBI hiding this from your broker?")
    assert not EnglishScriptRewriterAgent._has_shock_anchor("is your money silently at risk?")


def test_trim_filler_keeps_number_and_drops_fluff():
    words = "Is your SIP actually really just losing ₹4,00,000 every single year?".split()
    trimmed = EnglishScriptRewriterAgent._trim_filler(words, 11)
    assert "₹4,00,000" in trimmed
    assert "actually" not in trimmed
    assert len(trimmed) <= len(words)


def test_trim_filler_never_breaks_minimum_length():
    words = "Is your fund actually losing money?".split()  # removing 'actually' leaves 5 words
    trimmed = EnglishScriptRewriterAgent._trim_filler(words, 11)
    assert trimmed == words  # kept original to stay >= 6 words


def test_hook_repair_keeps_question_format_and_anchor():
    agent = EnglishScriptRewriterAgent()
    scenes = _scenes("Is your mutual fund distributor quietly skimming 1% commission every year from your account?")
    out = agent._fix_hook(scenes, topic="mutual fund commission")
    hook = out[0]["narration"]
    assert hook.endswith("?")
    assert "you" in hook.lower() or "your" in hook.lower()
    assert "1%" in hook or "1" in hook  # numeric anchor survived the trim


def test_hook_repair_preserves_named_investor_anchor():
    agent = EnglishScriptRewriterAgent()
    scenes = _scenes("Did Jhunjhunwala really hold Titan through five market crashes while you sold early?")
    out = agent._fix_hook(scenes, topic="Jhunjhunwala Titan holding")
    hook = out[0]["narration"]
    assert hook.endswith("?")
    assert EnglishScriptRewriterAgent._has_shock_anchor(hook)


def test_too_short_hook_replaced_with_topic_question():
    agent = EnglishScriptRewriterAgent()
    scenes = _scenes("Why gold?")
    out = agent._fix_hook(scenes, topic="gold loan auction risk")
    hook = out[0]["narration"]
    assert hook.endswith("?")
    assert len(hook.split()) >= 6
