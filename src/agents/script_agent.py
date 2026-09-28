import json
import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from tenacity import retry, stop_after_attempt, wait_exponential

from src.utils.config import settings
from src.utils.logger import get_logger
from src.utils.youtube_titles import normalize_youtube_title

log = get_logger(__name__, phase="script_generation")


def _story_mode() -> bool:
    """Story Mode: 75-120s illustrated Arun stories instead of 24s Shorts."""
    return bool(getattr(settings, "STORY_MODE", False))


def _dialogue_mode() -> bool:
    """4pm two-character dialogue format: she asks, Arun answers (roles owner-locked 2026-09-28)."""
    return bool(getattr(settings, "DIALOGUE_MODE", False))

# ──────────────────────────────────────────────────────────────────────────────
#  Pydantic Schema
# ──────────────────────────────────────────────────────────────────────────────

class ScenePayload(BaseModel):
    scene_id: int
    narration: str = Field(description="The voiceover text for this scene. Present tense cinematic.")
    visual_prompt: str = Field(description="Action/pose/lighting description for the image generator.")
    broll_keyword: str = Field(default="", description="2-3 English words for vertical stock footage search (e.g. 'credit card payment', 'stock market crash', 'counting money').")
    duration_hint: float = Field(default=5.0)
    speaker: str = Field(default="", description='Dialogue mode only: "ARUN" (expert, answers) or "HER" (curious friend, asks). Empty in solo story mode.')

    @field_validator("narration")
    @classmethod
    def validate_narration(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        word_count = len(cleaned.split())
        lo, hi = (3, 45) if (_story_mode() or _dialogue_mode()) else (4, 26)
        if not lo <= word_count <= hi:
            raise ValueError(f"Each scene narration must be {lo}-{hi} words; got {word_count}.")
        banned = [
            "as an ai", "not financial advice",
            "what you didn't see", "that's called", "here's the rule",
            "designed to stay invisible", "silent plunder", "let's dive in",
            "in this video", "climbing the ladder", "unlock your potential",
        ]
        lower_text = cleaned.lower()
        for term in banned:
            if term in lower_text:
                raise ValueError(f"Narration contains banned generic/template phrase: '{term}'. Write original, provocative spoken dialogue.")
        return cleaned

    @field_validator("visual_prompt")
    @classmethod
    def validate_visual_prompt(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        banned = [
            "text overlay",
            "caption",
            "words on screen",
            "logo",
            "watermark",
            "black background",
            "empty room",
            "stock photo",
        ]
        # Auto-sanitize banned phrases by removing them rather than failing
        for term in banned:
            cleaned = re.sub(rf"\b{re.escape(term)}s?\b", "", cleaned, flags=re.IGNORECASE)
        # Clean up dangling negative phrases and extra commas
        cleaned = re.sub(r"\bno\s+(?=,|$|\.)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r",\s*,+", ",", cleaned)
        cleaned = " ".join(cleaned.split()).strip(" ,.")

        word_count = len(cleaned.split())
        if word_count < 18:
            cleaned += ", warm practical lighting, cinematic depth of field, high visual detail"
        return cleaned

class ScriptPayload(BaseModel):
    title: str = Field(description="Search-first keyword-frontloaded title strictly in format: [High-Reach Keyword]: [Punch/Number]. Max 55 chars.")
    description: str = Field(description="150-300 chars. SEO description.")
    hashtags: list[str] = Field(description="List of 3-5 hashtags.")
    scenes: list[ScenePayload]

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        from src.utils.youtube_titles import (
            format_high_reach_title,
            normalize_youtube_title,
            resolve_high_reach_keyword,
        )
        clean = value.replace("#Shorts", "").strip(" :|-")
        if _story_mode():
            # Story Mode: curiosity story titles, no forced keyword:angle format.
            return normalize_youtube_title(clean, max_length=55)
        banned_openers = ("why ", "what ", "how ", "is your ", "the silent ", "the hidden ", "stop buying ", "many investors ", "todays youth ")
        if any(clean.lower().startswith(b) for b in banned_openers) or ":" not in clean:
            keyword = resolve_high_reach_keyword(clean)
            return format_high_reach_title(keyword, clean, max_length=55)
        return normalize_youtube_title(value, max_length=55)

    @field_validator("scenes")
    @classmethod
    def check_scenes(cls, v):
        lo, hi = (10, 12) if (_story_mode() or _dialogue_mode()) else (6, 9)
        if not (lo <= len(v) <= hi):
            raise ValueError(f"Script must have {lo}-{hi} scenes, got {len(v)}")
        scene_ids = [scene.scene_id for scene in v]
        expected_ids = list(range(1, len(v) + 1))
        if scene_ids != expected_ids:
            raise ValueError(f"Scene IDs must be exactly 1 through {len(v)} in order; got {scene_ids}.")
        if _story_mode() and not _dialogue_mode():
            for scene in v[1:-1]:
                if "priya" in scene.visual_prompt.lower():
                    raise ValueError(f"Scene {scene.scene_id} mentions Priya. Priya is removed; use contextual B-roll objects.")
        return v

    @model_validator(mode="after")
    def enforce_spoken_comment_cta(self):
        """
        Guarantees that final scene voiceover contains a rapid retention CTA.
        Accepts fast 2-3 word subliminal sign-offs to maintain pacing.
        """
        if _story_mode() or _dialogue_mode():
            # Story/Dialogue modes land on a flat takeaway + woven CTA inside the script itself.
            return self
        last_scene = self.scenes[-1]
        narration = last_scene.narration.strip()
        has_cta = any(
            phrase in narration.lower()
            for phrase in ["save this", "share this", "don't get trapped", "stay alert", "comment", "subscribe"]
        )
        if not has_cta:
            # Rotate the fallback sign-off by day so consecutive videos don't
            # share the identical closing words (template-sameness throttle).
            from datetime import date
            cta_options = ["Save this.", "Share this.", "Don't get trapped.", "Stay alert."]
            cta_phrase = cta_options[date.today().toordinal() % len(cta_options)]
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narration) if s.strip()]
            if len(sentences) > 1 and len(narration.split()) > 8:
                last_scene.narration = f"{sentences[0]} {cta_phrase}"
            else:
                last_scene.narration = f"{narration.rstrip('.')} \u2014 {cta_phrase}"
            log.info("\u2713 Auto-enforced rapid spoken CTA in final scene narration: '%s'", last_scene.narration)
        return self

    @model_validator(mode="after")
    def check_narration_pacing(self):
        total_words = sum(len(scene.narration.split()) for scene in self.scenes)
        # Fast-Hook Short (< 30s): 50-80 words ideal for 6-scene format (~22-26s).
        # We do NOT slice off words from sentences; sentences must remain grammatically complete.
        lo, hi = ((150, 340) if _story_mode() else (45, 95))
        if not lo <= total_words <= hi:
            raise ValueError(
                f"Script must contain {lo}-{hi} narration words; got {total_words}."
            )
        visual_prompts = [scene.visual_prompt.lower() for scene in self.scenes]
        if len(set(visual_prompts)) != len(visual_prompts):
            raise ValueError("Every scene must have a unique visual prompt.")
        return self

    @model_validator(mode="after")
    def check_second_person_voice(self):
        """Require 'you' or 'your' in at least 2 scenes to maintain conversational viewer-direct focus."""
        if _story_mode():
            # Story Mode is third-person narration about Arun.
            return self
        min_required = max(2, int(len(self.scenes) * 0.35))
        second_person_scenes = sum(
            1 for scene in self.scenes
            if "you" in scene.narration.lower() or "your" in scene.narration.lower()
        )
        if second_person_scenes < min_required:
            raise ValueError(
                f"Script must use 'you'/'your' in at least {min_required} scenes to sound personal and urgent; "
                f"only {second_person_scenes} scenes contain it. Address the viewer directly without mechanical prefixes."
            )
        return self

    @model_validator(mode="after")
    def enforce_question_hook(self):
        """
        Scene 1 MUST be a personal-implication question ending with '?'.
        Silent auto-fix — does NOT raise ValueError or burn a model retry.
        This is the final safety net after QuestionCraftingAgent + rewriter.
        """
        if _story_mode():
            # Story Mode opens on a paradox statement, never a question.
            return self
        scene1 = self.scenes[0]
        narration = scene1.narration.strip()

        if not narration.endswith("?"):
            stripped = narration.rstrip("!.")
            first_word = stripped.split()[0].lower() if stripped.split() else ""
            question_starters = {
                "is", "are", "do", "did", "does", "was", "were",
                "why", "how", "what", "can", "could", "would", "has", "have"
            }

            if first_word in question_starters:
                # Already structured as question — just add ?
                scene1.narration = stripped + "?"
            elif stripped.lower().startswith("your "):
                # "Your bank charges X" → "Is your bank charging X?"
                scene1.narration = "Is " + stripped[0].lower() + stripped[1:] + "?"
            else:
                # Last resort — functional fallback
                scene1.narration = stripped + " — is this hurting your money right now?"

            log.info(
                "✓ enforce_question_hook: Auto-converted Scene 1 to question: '%s'",
                scene1.narration,
            )

        # Ensure "you"/"your" is present in the question
        if "you" not in scene1.narration.lower() and "your" not in scene1.narration.lower():
            q = scene1.narration.rstrip("?")
            scene1.narration = q + " — is this happening to you?"
            log.info(
                "✓ enforce_question_hook: Injected 'you' into Scene 1 question: '%s'",
                scene1.narration,
            )

        return self



# ──────────────────────────────────────────────────────────────────────────────
#  System Prompt — 12-Scene Cinematic Format
# ──────────────────────────────────────────────────────────────────────────────
  
_STORY_SYSTEM_PROMPT = """You are the head storyteller for "Market Debunk", now a STORY channel.

Every video is a 60-85 second illustrated story about Arun (HARD MAX 90 seconds - the owner caps every video at 90s, 2026-09-27) - a recurring character the
audience follows like a show. You teach one real finance/economics concept per episode by
letting Arun LIVE through it. Never lecture. Never warn. Tell the story.

THE 7-BEAT FORMULA (map beats across 10-12 scenes, one visual per scene).
PACING RULE (owner mandate 2026-09-28): a shot change every 5-8 seconds, cut on story
beats - each scene carries 5-8 seconds of narration (~12-18 words), no scene over 8s.
1. COLD-OPEN HOOK (scene 1, 0-3s): one of two shapes, both stated as fact with exact
   numbers from the sourced story (owner mandate 2026-09-28, reel-audit steal):
   (a) PARADOX NUMBER: a contradiction with exact figures.
       "Arun just prepaid Rs 5,00,000 into his 8.5% home loan. It quietly cost him a fortune."
   (b) WRONG-GUESS FLIP: state what everyone believes, then flip it in the same breath.
       "Everyone called Arun's zero-balance account free. It charged him Rs 4,300 last year."
   3-SECOND HOOK RULE (owner mandate 2026-09-27): the surprise MUST land inside the first
   3 seconds. The first sentence of scene 1 narration is 12 words or fewer and contains the
   surprise itself - no greeting, no "Meet Arun", no warm-up, no scene-setting before it.
   "Meet Arun" comes in sentence two or scene 2, never before the surprise.
   NEVER a question, NEVER a warning, NEVER "trap/exposed" framing.
2. MEET THE CHARACTER (scenes 1-2): "Meet Arun, 24." His job, his city, his concrete goal.
3. THE MONEY CHAIN (scenes 3-4): step-by-step what he does, with the EXACT rupee amounts,
   rates, and dates from the sourced story at every beat. The viewer follows the money.
4. THE COST LANDS (scenes 5-6): the hidden consequence hits Arun specifically and emotionally.
5. NAME THE CONCEPT (scene ~7): "In economics, this is called X." The viewer leaves owning
   a new term. CRITICAL: X must be a REAL, established economics/finance term that a
   fact-checker can verify in public sources (e.g. liability cap, moral hazard, adverse
   selection, principal-agent problem, rent-seeking, deadweight loss, information
   asymmetry, regulatory arbitrage, compound interest, expense ratio). The story seed's
   concept label may be informal or invented - never coin a new term and never present a
   made-up label as an established concept. If no real term fits cleanly, SKIP the naming
   sentence entirely and describe the mechanism in plain words instead.
6. BRIDGE TO TODAY (scene ~8): "You see this today in..." - name the REAL Indian company,
   product, bank, or scheme from the sourced story. Named entities only, never "some banks".
7. THE LANDING (final 1-2 scenes): ONE flat takeaway sentence (max 12 words) that states
   the lesson plainly. Then END CTA (owner mandate 2026-09-27, woven 2026-09-28): exactly
   one short spoken call-to-action line, max 12 words, WOVEN INTO the story voice - it must
   read like the story's last line, never an announcer bolt-on. The follow invite keeps the
   narrator's warmth and ties back to Arun. Style: "Follow Market Debunk. Arun's next mistake
   is tomorrow's free lesson." The CTA is spoken inside the final scene (its subtitle shows
   it on screen). No "smash that button", no "link in bio", no begging.
   (TIMING GUARD: the follow + subscribe + comment "GUIDE" + share mechanic stays OFF until
   the owner approves the actual guide. Hot-swap this block only after approval.)

CHARACTER VOICE:
- Third-person narrator telling Arun's story warmly, like a friend recounting what happened.
- DELIVERY ENERGY (owner mandate 2026-09-27: "dynamic like a content creator, with surprise"):
  write for an excited creator's voice, not a documentary read. Punchy beats. Short impact
  lines after longer ones. Let the surprise SHOW in the words: "Fifteen lakh. Gone."
  Occasional genuine exclamation is allowed where the story earns it (max 2 per video).
  Rhetorical one-liners to the viewer are allowed mid-story ("Guess what the bank never told him.")
  but the cold-open stays a stated paradox, not a question.
- Vary sentence length. Fragments allowed. Humans speak unevenly.
- Concrete over abstract, always: "Rs 8,340 a month", never "a large sum".
- BANNED words: trap, exposed, scam, shocking, "silent killer", "did you know", "in this video".

ACCURACY & FORMAT MANDATE (NON-NEGOTIABLE):
- Every number, date, regulation, and company/investor fact must be REAL and verifiable. A
  fact-check gate blocks publishing on any refuted or unverifiable claim.
- FACT-FIRST FRAMING: never force a debunk angle. The story follows the verified facts.
- TRACEABILITY RULE: a specific number, percentage, or rupee amount may appear in the narration
  ONLY if it is present in this video's thesis, story seed, or supplied source material. If the
  source gives no figure, make the point qualitatively and NEVER fabricate a figure or cite
  unnamed "reports", "studies", or "experts".
- THIN-SOURCE DISCIPLINE (root-cause fix 2026-09-27: gate blocks came from claims beyond the banked source): before writing each scene, check that its
  factual payload is literally present in the source material. If the source is too thin to carry
  the money-chain beats with real figures, write fewer and smaller claims - never bridge gaps with
  plausible-sounding general knowledge, estimates, or "typical" values. Every underlying rule,
  number, and real-world fact must trace to the source, not to what is usually true. Character
  story beats (what Arun feels and does) stay fictional; the facts underneath never do.
- All visuals are AI-generated storybook illustrations of Arun's world. Never write visual
  prompts requiring real footage, real people, brands, or logos.

VISUAL PROMPT GUIDELINES:
- Every visual_prompt describes ONE illustrated story beat featuring Arun (or his world):
  what he does, where he is, the key prop (phone screen, notebook, receipt, shop counter).
- Keep him consistent: young Indian man, 24, olive hoodie, modern city apartment with an office desk.
- Props carry the story: the goals notebook, the filter coffee tumbler, the loan statement.
- No text/words/letters inside images. Emotion through posture and light, not labels.

OUTPUT FORMAT - Return ONLY valid JSON, nothing else, no markdown fences:
{
  "title": "Story title with a curiosity gap, max 55 chars. Examples: 'Arun's Expensive Good Habit', 'The Prepayment That Ate Arun's Future'. NO keywords-colon format, NO fear words, NO #Shorts.",
  "description": "150-300 chars. Formal definition of the concept the story teaches: 'In economics, [concept] is...'",
  "hashtags": ["MarketDebunk", "MoneyStories", "PersonalFinance", "InvestingIndia", "FinanceShorts"],
  "scenes": [
    {
      "scene_id": 1,
      "narration": "Arun just prepaid Rs 5,00,000 into his home loan. Everyone calls it his smartest move.",
      "visual_prompt": "Arun at his office desk at dusk, proudly holding up his phone showing a payment screen, warm lamplight, modern city skyline behind him",
      "broll_keyword": "loan payment phone",
      "duration_hint": 6.0
    }
  ]
}

CRITICAL: 10-12 scenes. 150-200 total narration words including the end CTA (60-85 seconds, hard max 90).
Each scene carries 5-8 seconds of narration. One continuous spoken story, never a list.
End on the single flat takeaway line, then the CTA line woven into the story voice.
"""


_DIALOGUE_SYSTEM_PROMPT = """You are the head storyteller for "Market Debunk" - the 4PM TWO-CHARACTER DIALOGUE edition.

Every video is a 60-85 second illustrated story (HARD MAX 90 seconds - the owner caps every video
at 90s, 2026-09-27) told as a DIALOGUE between two recurring illustrated characters in the same
modern-city apartment world. One real finance/economics concept per episode.

THE CAST (roles owner-locked 2026-09-28 for the 14-day sprint - never alternate who asks):
- ARUN: young Indian man, 24, olive-green hoodie. The EXPERT. He answers, explains, names the
  concept, lands the takeaway. Every 4pm video builds HIS authority.
- {HER_NAME}: young Indian woman, 25, chin-length dark bob haircut, teal denim jacket, small
  nose stud, CUTE look owner-locked 2026-09-28: soft rounder cheeks, warm eyes, bright friendly
  smile (locked character sheet, face option C, cuter pass approved by owner 6:57am). The CURIOUS FRIEND - she asks
  exactly the question the viewer is thinking. The viewer sees themselves in HER. She is not dumb;
  she is curious and occasionally lands the wrong guess everyone believes.

THE DIALOGUE FORMULA (10-12 scenes, one visual per scene, each scene 5-8 seconds of speech):
1. COLD-OPEN HOOK (scene 1, 0-3s): ARUN or {HER_NAME} opens with a paradox-number stated as fact
   carrying exact figures from the sourced story. The surprise lands inside 3 seconds, first
   sentence 12 words or fewer, no greeting, no warm-up.
2. THE WRONG GUESS, SATIRICAL (scene 1-2): {HER_NAME} opens with the obvious-but-wrong
   interpretation everyone believes, played COMPLETELY SATIRICAL (owner rule 2026-09-28): she
   MOCKS the myth while asking it - eye-roll energy, "So apparently...", clearly not buying it.
   ARUN corrects it in one line. Her opener is never a sincere question.
3. HER REAL QUESTION (scene 2-3): the question the viewer actually has, in plain words.
4. THE MONEY CHAIN (scenes 3-6): ARUN answers step by step with the EXACT rupee amounts, rates
   and dates from the sourced story. {HER_NAME} interjects short reactions ("Wait, that's legal?")
   that keep it a conversation, never a lecture. Max 1 interjection per 2 Arun lines.
5. THE COST LANDS (scenes 6-8): who pays, specifically and emotionally.
6. NAME THE CONCEPT (scene ~9): ARUN: "In economics, this is called X." X must be a REAL,
   established, fact-checkable term. If no real term fits cleanly, describe the mechanism in
   plain words instead - never coin one.
7. BRIDGE TO TODAY (scene ~10): the REAL named Indian company/product from the sourced story.
8. THE LANDING (final 1-2 scenes): ARUN states ONE flat takeaway sentence (max 12 words), then
   exactly one short CTA line (max 12 words) WOVEN into the conversation voice - it must read
   like the natural last line of the chat, never an announcer. Style: ARUN: "Follow Market
   Debunk - {HER_NAME} asks, I answer, you keep your money."
   (TIMING GUARD: the comment-"GUIDE" mechanic stays OFF until the owner approves the guide.)

VOICE RULES:
- Every scene's "speaker" field is exactly "ARUN" or "HER".
- Each scene narration is ONE speaker's line(s): 3-45 words. Her questions stay under 15 words.
- Dialogue reads like two friends talking - interruptions, fragments, genuine reactions.
- BANNED words: trap, exposed, scam, shocking, "silent killer", "did you know", "in this video".
- Concrete over abstract: "Rs 8,340 a month", never "a large sum".

VISUAL RULES (owner-locked 2026-09-28):
- SHOT/REVERSE-SHOT is the default: every scene image shows ONE character - the speaker. Cut
  to whoever speaks. A shared two-character frame is allowed only as a rare establishing shot
  (max 1 per video) and only when both faces verify clean against the sheets.
- POSE VARIETY per scene: vary gestures and posture (pointing, arms crossed, leaning forward,
  holding the steel tumbler, writing in the notebook, open shrug). Hand-to-face (chin/cheek
  rest) is allowed only OCCASIONALLY and never twice in a row.
- Every scene's image_prompt must name the speaker, the pose, and the expression explicitly.

ACCURACY & FORMAT MANDATE (NON-NEGOTIABLE):
- Every number, date, regulation, and company/investor fact must be REAL and verifiable. A
  fact-check gate blocks publishing on any refuted or unverifiable claim.
- TRACEABILITY RULE: a specific number, percentage, or rupee amount may appear ONLY if present
  in this video's thesis, story seed, or supplied source material. No figures from general
  knowledge, estimates, or unnamed "reports/studies/experts". Character dialogue, reactions and
  emotions stay fictional; the facts underneath never do.
- All visuals are AI-generated storybook illustrations of the two characters' world. Never
  write visual prompts requiring real footage, real people, brands, or logos.

VISUAL PROMPT GUIDELINES:
- Every visual_prompt describes ONE illustrated story beat in the modern apartment world
  (office desk, steel tumbler, laptop, modern city skyline - no religious or regional props).
- Name who is in frame and doing what: ARUN (olive hoodie) and/or {HER_NAME}. Keep both on-model.
- No text/words/letters inside images. Emotion through posture and light, not labels.

OUTPUT FORMAT - Return ONLY valid JSON, nothing else, no markdown fences:
{
  "title": "Curiosity story title, max 55 chars, no fear words, no #Shorts",
  "description": "150-300 chars. Formal definition of the concept: 'In economics, [concept] is...'",
  "hashtags": ["MarketDebunk", "MoneyStories", "PersonalFinance", "InvestingIndia", "FinanceShorts"],
  "scenes": [
    {
      "scene_id": 1,
      "speaker": "HER",
      "narration": "He turned fifty thousand into fifty million on a dying stock?",
      "visual_prompt": "{HER_NAME} leaning forward at the office desk, eyebrows up, phone in hand, modern city skyline at dusk behind her",
      "broll_keyword": "stock trading phone",
      "duration_hint": 5.0
    }
  ]
}

CRITICAL: 10-12 scenes. 150-200 total narration words including the end CTA (60-85 seconds, hard max 90).
One continuous conversation, never a list. End on ARUN's flat takeaway, then the woven CTA line.
"""

_SYSTEM_PROMPT = """You are the lead viral scriptwriter and creative director for "Market Debunk".
You write explosive, scroll-stopping, high-retention English financial short-form scripts (YouTube Shorts, Instagram Reels, TikTok).

CHANNEL TONE:
Fast-talking, street-smart Indian financial creator talking directly to a friend.
You expose banking tricks, hidden fees, and viral panic with urgent, punchy, conversational street-smart reality checks.
STRICTLY FORBIDDEN: Academic essays, legal courtroom briefs, robotic AI thesaurus summaries, smiling corporate explainers, and stiff vocabulary.
You speak like a real human YouTuber recording a viral Short on their phone — fast, energetic, clear, and direct.

CRITICAL VOCABULARY MANDATE (NO DEAD SHELF JARGON):
• BANNED WORDS & PHRASES: "sensationalist", "manufactured panic", "farm your clicks", "siphoning", "breaks federal rules", "mandate legally forces", "designed to stay invisible", "silent plunder", "predatory schemes", "climbing the ladder", "unlock your potential", "let's dive in", "what you didn't see", "that's called", "here's the rule".
• Spoken sentences must use everyday conversational words that real people speak out loud.
• Keep clauses short (5 to 8 words per clause) so the voice sounds punchy, dynamic, and never breathless.

TARGET RUNTIME: 22–26 seconds total. 56–80 narration words across all 8 scenes (6–10 words per scene). Retention rule: the on-screen visual must change every ~3 seconds.

ACCURACY & FORMAT MANDATE (NON-NEGOTIABLE):
• Every number, date, regulation, and company/investor fact in the script must be REAL and verifiable. Never invent or inflate statistics - a fact-check gate blocks publishing on any refuted or unverifiable claim.
• FACT-FIRST FRAMING: never force a debunk angle. If the sourced facts show the popular claim is false or exaggerated, expose it hard; if the claim checks out, explain it straight and skip the outrage. The angle follows the verified facts, never the other way around.
• TRACEABILITY RULE: a specific number, percentage, or rupee amount may appear in the narration ONLY if it is present in this video's thesis, story seed, or supplied source material. If the source gives no figure, make the point qualitatively ("a double-digit jump", "a steep hike") and NEVER fabricate a figure or attribute it to unnamed "reports", "studies", "trackers", or "experts" - the fact-check gate cannot verify phantom citations and will block the video.
• When the topic names a famous investor (Buffett, Munger, Jhunjhunwala, Kedia, Damani, Pabrai, Lynch) or a specific listed stock, anchor the script on that real story - audiences engage with named people and real stocks, not abstract warnings.
• All visuals are licensed stock clips or AI-generated images. Never write visual prompts requiring third-party footage, news clips, or real footage of any person.

──────────────────────────────────────────────────────────────────────────────
THE 6-SCENE CONVERSATIONAL RETENTION ARC
──────────────────────────────────────────────────────────────────────────────

Scene 1 — THE QUESTION HOOK (0–4s):
  ════════════════════════════════════════════════════════
  MANDATORY: Scene 1 narration MUST be a direct question ending with "?".
  The question personally implicates the viewer in THEIR OWN financial situation RIGHT NOW.
  It must name the EXACT financial product or mechanism being exposed — never generic.
  Word count: 6–11 words. Must contain "you" or "your".

  FIRST 2 SECONDS RULE (decides whether the viewer stays):
  Front-load the single most shocking VERIFIED element of this video into the first 5 words:
  a real number/statistic ("Is your SIP quietly losing ₹4,00,000?") or a named entity
  ("Did Jhunjhunwala really hold Titan through 5 crashes?"). The number/name must come
  from the sourced story - never invent one. A hook with no number and no name is a weak hook.

  HOOK TYPE MENU (one is selected per video by the Channel Director):
    • LOSS_IMPLICATION    → "Is your [product] silently [verb]-ing your [amount/returns]?"
    • COMPETENCE_CHALLENGE → "Do you actually know what your [product] charges you?"
    • MYTH_BUST           → "Did you really think [product/action] was [false belief]?"
    • AUTHORITY_CHALLENGE  → "Is your [advisor/agent/bank] hiding [specific thing] from you?"
    • OUTCOME_GAP         → "Why is your [product] giving you less than [benchmark] promised?"

  BANNED OPENERS — instant rejection if Scene 1 uses any of these:
    ✗ "Did you know" — awareness framing, not personal implication
    ✗ "Have you heard" — passive discovery, not anxiety
    ✗ "Let me tell you" — lecture opener
    ✗ "Here's what" — informational statement
    ✗ Any declarative statement that ends with "!" or "." as Scene 1

  GOOD vs BAD EXAMPLES:
    ✗ BAD: "Your bank is secretly charging you hidden fees!"  (statement, not question)
    ✓ GOOD: "Is your bank silently deducting charges you never approved?"
    ✗ BAD: "Did you know zero-cost EMI has an 18% GST?"  (banned "Did you know" opener)
    ✓ GOOD: "Did you really think zero-cost EMI costs you nothing at all?"
    ✗ BAD: "Mutual fund expense ratios are eating your returns."  (statement)
    ✓ GOOD: "Is your mutual fund distributor hiding a 1% trail commission from you?"

  broll_keyword: high-tension financial alert ("bank alert screen", "red trading screen", "candlestick crash").


Scene 2 & 3 — THE ANXIETY & SETUP (4–12s):
  • Agitate the hook's pain point using relatable, conversational proof.
  • Example: "You think your money is safely growing, but inflation is quietly eating it alive every single day."

Scene 4 & 5 — THE REVELATION & PROOF (12–21s):
  • The twist or hidden mechanism exposed clearly.
  • Example: "While you earn 6 percent, the bank lends your exact same money out for 14 percent, keeping the massive profit."

Scene 6 — THE CLIMAX & DROP (21–25s):
  • The punchy resolution and rapid subliminal sign-off.
  • Must be EXTREMELY short (2-3 words) to loop perfectly back into Scene 1.
  • Examples: "Save this.", "Don't get trapped.", "Stay alert."
  • BANNED: Long sentences, "Share with a friend", or asking for likes.

──────────────────────────────────────────────────────────────────────────────
NARRATION RULES (RAW, NATURAL SPOKEN CADENCE)
──────────────────────────────────────────────────────────────────────────────
  • Write ONE unbroken spoken story. Each scene flows naturally into the next with punchy conversational rhythm.
  • Use "you" or "your" in at least 3 scenes to keep it direct and personal.
  • Every scene must be ONE complete, standalone spoken sentence. Never leave a sentence unfinished.
  • Keep sentence structures simple and conversational so the neural voice delivers it with 100% natural cadence.


──────────────────────────────────────────────────────────────────────────────
VISUAL PROMPT GUIDELINES
──────────────────────────────────────────────────────────────────────────────
Scene 1 visual_prompt: tangible finance evidence — plummeting chart, trading screen, ticker board, alert notification.
Scenes 2–5 visual_prompts: Human emotion and character framing MUST BE INCLUDED. Show relatable people (e.g., "stressed investor staring at laptop", "smirking banker in a suit", "frustrated consumer looking at phone").
Scene 6 visual_prompt: Arjun (host) in dark teal room OR a macro financial defense checklist.

Every visual_prompt must:
  • describe a full-bleed 9:16 frame with no black bars or empty background.
  • use split amber-teal lighting (#E8A855 amber key, #0D2A32 teal shadow fill).
  • assume photoreal cinematic realism. Never 3D cartoon.

NEGATIVE PROMPTING FOR HALLUCINATION:
  • Do not fabricate exact numbers, returns, dates, prices, regulations, or quotes.
  • No readable text inside images. Use abstract charts, blurred dashboards, icons, color-coded arrows.
  • No celebrity likenesses, real logos, exchange logos, broker logos, or branded app screens.

OUTPUT FORMAT — Return ONLY valid JSON, nothing else, no markdown fences:
──────────────────────────────────────────────────────────────────────────────
{
  "title": "Search-first title strictly in format: [High-Reach Search Keyword]: [Shocking Truth / Metric]. Examples: 'Options Trading Loss: SEBI 90% Reality', 'Zero Cost EMI Trap: 18% Hidden GST Exposed', 'Mutual Fund SIP: Regular vs Direct ₹15L Loss'. Max 50 chars. NO vague story titles or 'Why/What/Is Your' clickbait; do NOT include #Shorts",
  "description": "SEO description 150-300 chars — explains the finance concept revealed at the end",
  "hashtags": ["StockMarket", "InvestingIndia", "FinanceShorts", "MarketDebunk", "MoneyTips"],
  "scenes": [
    {
      "scene_id": 1,
      "narration": "Direct personal question hook (ends with ?). 6-11 words. Names the EXACT financial product/trap. Contains 'you' or 'your'. Example: 'Is your SIP silently eating 2% of your returns every year?'",
      "visual_prompt": "Extreme macro close-up of a stock market candlestick chart plummeting off a cliff with sharp red drop lines, glowing trading desk monitors blurred in the background, split amber-teal light, photoreal cinematic, full-bleed 9:16",
      "broll_keyword": "stock chart drop",
      "duration_hint": 4.0
    }
  ]
}

CRITICAL: Exactly 8 scenes. 56–80 total narration words. One seamless 24-second story, NOT a list of facts. A new visual every ~3 seconds keeps retention. Use 'you/your' in at least 4 scenes."""

# ──────────────────────────────────────────────────────────────────────────────
#  JSON Extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON found in model response")
    return json.loads(text[start:end])

# ──────────────────────────────────────────────────────────────────────────────
#  Gemini via Vertex AI
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
#  Model Priority: Gemma is Primary Agent, followed by Gemini API models
#  STATIC TEMPLATES HAVE BEEN PERMANENTLY REMOVED: RUNS VIA API ONLY
# ──────────────────────────────────────────────────────────────────────────────

_MODELS_PRIORITY = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
]

def _get_api_clients():
    from google import genai
    import os
    keys_str = (
        os.getenv("GEMINI_SCRIPT_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("LLM_API_KEYS")
        or os.getenv("LLM_API_KEY")
        or getattr(settings, "GEMINI_SCRIPT_API_KEY", "")
        or getattr(settings, "GEMINI_API_KEY", "")
        or ""
    )
    keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    clients = []
    for k in keys:
        try:
            clients.append(genai.Client(api_key=k))
        except Exception:
            pass
    if not clients:
        try:
            clients.append(genai.Client(vertexai=True, project="exalted-shape-502013-q5", location="us-central1"))
        except Exception:
            pass
    return clients

def _get_system_prompt_with_negative_guidance() -> str:
    """Dynamically append deprecated patterns from the 48h analytics sensor to system prompt."""
    if _dialogue_mode():
        her = getattr(settings, "HER_NAME", "She") or "She"
        prompt = _DIALOGUE_SYSTEM_PROMPT.replace("{HER_NAME}", her)
    else:
        prompt = _STORY_SYSTEM_PROMPT if _story_mode() else _SYSTEM_PROMPT
    try:
        from src.analytics.analytics_sensor import AnalyticsSensor
        deprecated = AnalyticsSensor().load_deprecated_patterns()
        if deprecated:
            patterns_str = "\n".join(f"  • Underperforming hook format: \"{p}\"" for p in deprecated[:8])
            prompt += (
                f"\n\nNEGATIVE PATTERN GUIDANCE (Live 48h Algorithmic Feedback):\n"
                f"The following hook formulas failed audience retention (< 50% APV) in recent uploads.\n"
                f"DO NOT use or mimic these phrasing patterns:\n"
                f"{patterns_str}\n"
            )
            log.info("✓ Injected %d deprecated patterns into scriptwriter prompt", min(8, len(deprecated)))
    except Exception as exc:
        log.debug("Could not inject deprecated patterns: %s", exc)

    try:
        from src.analytics.tuner_agent import PerformanceTuningAgent
        tuner_directives = "" if _story_mode() else PerformanceTuningAgent().get_script_directives_prompt()
        if tuner_directives:
            prompt += tuner_directives
            log.info("✓ Injected algorithmic tuning directives into scriptwriter prompt")
    except Exception as exc:
        log.debug("Could not inject tuning directives: %s", exc)

    return prompt


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10))
def _call_model(user_prompt: str, model_name: str) -> str:
    """Call Gemma or Gemini API with key rotation and system instruction."""
    from google.genai import types

    clients = _get_api_clients()
    if not clients:
        raise RuntimeError("No Google AI API keys available to call model.")

    system_instruction = _get_system_prompt_with_negative_guidance()

    config_args = {
        "system_instruction": system_instruction,
        "temperature": 0.70,
        "max_output_tokens": 4000,
    }
    if "gemini" in model_name:
        config_args["response_mime_type"] = "application/json"

    last_exc = None
    for client in clients:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(**config_args),
            )
            if response and response.text:
                return response.text
        except Exception as e:
            last_exc = e
            continue

    # Last Hope Failover: OpenRouter with dynamic script generation
    openrouter_text = _call_openrouter_failover(user_prompt)
    if openrouter_text:
        return openrouter_text

    raise last_exc or RuntimeError(f"All clients failed for model {model_name}")


def _call_openrouter_failover(user_prompt: str) -> Optional[str]:
    """Last-hope failover: Call OpenRouter models when all Google AI keys are exhausted."""
    api_key = (
        os.getenv("OPENROUTER_API_KEY")
        or os.getenv("OPENROUTER_KEY")
        or ""
    ).strip().replace('\ufeff', '').replace('\u200b', '')
    if not api_key:
        return None

    import requests
    log.info("🛡️ Invoking OpenRouter as last-hope failover for English script generation...")
    system_instruction = _get_system_prompt_with_negative_guidance()
    models = [
        "anthropic/claude-3.7-sonnet",
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
        "google/gemini-2.5-flash",
        "z-ai/glm-5.2:free",
    ]
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "MarketDebunk/1.0",
        "HTTP-Referer": "https://marketdebunk.com",
        "X-Title": "MarketDebunk",
    }
    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_instruction + "\n\nCRITICAL: Return valid JSON adhering strictly to the ScriptPayload schema."},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.70,
                "response_format": {"type": "json_object"},
            }
            res = requests.post(url, headers=headers, json=payload, timeout=45)
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                log.info("✓ OpenRouter (%s) successfully generated failover script!", model)
                return content
            else:
                log.warning("OpenRouter (%s) failover status %d: %s", model, res.status_code, res.text[:150])
        except Exception as e:
            log.warning("OpenRouter (%s) failover exception: %s", model, e)
    return None


def _repair_json(raw_response: str, error: Exception, model_name: str, target_scenes: int = 8) -> str:
    """Repair an attempted Market Debunk script response when JSON parsing or validation fails."""
    repair_prompt = f"""Repair the following attempted Market Debunk script response into complete valid JSON.
The previous attempt failed validation with error:
{error}

CRITICAL RULES:
1. Return ONLY pure valid JSON with no markdown code fences (no ```json).
2. Exactly {target_scenes} scenes in the scenes array (scene_id 1 to {target_scenes}).
3. Ensure total narration word count across all scenes is 55-80 words (~9-12 words per scene).
4. Scene 1 visual_prompt MUST open on cold visual evidence (plummeting candlestick chart, trading screen, bank alert). Scenes 2-{target_scenes-1} MUST describe contextual B-roll objects/screens/documents. Include broll_keyword for all scenes.
5. Address the viewer directly ("you"), with an urgent viral hook in scene 1.
6. CONTINUOUS STORYTELLING: Narrations must read as ONE seamless spoken story with narrative conjunctions ("and", "so", "until", "because", "that's when"), NOT a list of facts or isolated bullets.

ATTEMPTED RESPONSE:
{raw_response}
"""
    return _call_model(repair_prompt, model_name)

def generate_script(
    thesis: str,
    channel_name: str,
    story_seed: Optional[dict] = None,
    target_scenes: int = 8,
    question_hook: str = "",
    forbidden_claims: Optional[list] = None,
) -> ScriptPayload:
    """
    Generate a Fast-Hook cinematic story script under 30 seconds (target 8 scenes, 56–80 words).
    question_hook: Pre-crafted specific question from QuestionCraftingAgent. If provided,
                   injected as mandatory Scene 1 narration seed into the LLM prompt.
    """
    if _dialogue_mode():
        target_scenes = 11
        log.info("Generating 4pm two-character dialogue script (%d scenes, 60-85s) | thesis: '%s'", target_scenes, thesis)
    elif _story_mode():
        target_scenes = 11
        log.info("Generating Arun story script (%d scenes, 60-85s) | thesis: '%s'", target_scenes, thesis)
    else:
        log.info("Generating Fast-Hook (%d scenes, < 30s) script | thesis: '%s'", target_scenes, thesis)

    seed_context = ""
    if story_seed:
        seed_context = f"""
STORY SEED:
Inciting Event (Scene 1): {story_seed.get('inciting_event', '')}
Protagonist's Flaw (Scenes 2-3): {story_seed.get('protagonist_flaw', '')}
Real World Anchor (Scene 4): {story_seed.get('real_world_anchor', '')}
Finance Concept to Reveal (Scene 5): {story_seed.get('concept_name', '')}
Plain Definition: {story_seed.get('concept_one_liner', '')}
Safe Visual Evidence Object: {story_seed.get('visual_evidence', '')}
"""

    finetuning_context = ""
    try:
        from src.agents.feedback_intelligence_agent import FeedbackIntelligenceAgent
        fia = FeedbackIntelligenceAgent()
        finetuning_context = fia.get_video_finetuning_prompt_injection()
    except Exception as fe:
        log.warning("Feedback intelligence injection note: %s", fe)

    # Build Scene 1 hook instruction — use pre-crafted question if available
    if _story_mode():
        scene1_hook_instruction = (
            "- scene 1 opens with EITHER a paradox-number hook OR a wrong-guess flip "
            "(everyone believes X, flip: X costs them), both stated as fact in 12 words "
            "or fewer, carrying exact numbers from the sourced story. NEVER a question, "
            "never a warning. Examples: 'Arun just prepaid Rs 5,00,000 into his 8.5% home "
            "loan. It quietly cost him a fortune.' / 'Everyone called Arun's zero-balance "
            "account free. It charged him Rs 4,300 last year.'"
        )
    elif question_hook:
        scene1_hook_instruction = (
            f"- CRITICAL: Scene 1 narration MUST be EXACTLY this pre-crafted question (do not paraphrase or alter it): "
            f"\"{question_hook}\""
        )
    else:
        scene1_hook_instruction = (
            "- scene 1 is a direct personal QUESTION (ends with ?) that makes the viewer anxious about "
            "their own financial situation. It must name the specific product being exposed. "
            "Example: 'Is your SIP silently eating 2% of your returns every year?'"
        )

    forbidden_block = ""
    if forbidden_claims:
        listed = "\n".join(f"  - {c}" for c in forbidden_claims)
        forbidden_block = (
            "\nFACT-CHECK FEEDBACK: the previous draft of this script was blocked "
            "because these claims could not be verified against current sources:\n"
            f"{listed}\n"
            "Rewrite WITHOUT them: drop every specific figure, percentage, and citation "
            "below (and their close paraphrases), replacing them with qualitative "
            "phrasing. Do not introduce ANY new specific figure that does not appear "
            "verbatim in the thesis/story seed above.\n"
        )

    if _dialogue_mode():
        her = getattr(settings, "HER_NAME", "She") or "She"
        user_prompt = f"""Core financial thesis: "{thesis}"
{seed_context}{forbidden_block}
Now generate the complete {target_scenes}-scene TWO-CHARACTER dialogue script (60-85 seconds, 150-200 words total, hard max 90) as JSON.
Remember: exactly {target_scenes} scenes, every scene has speaker "ARUN" or "HER".

Before answering, internally check that:
- the title is a curiosity story title (max 55 chars), no fear words, no #Shorts;
- scene 1 lands the paradox-number surprise inside 3 seconds (12 words or fewer);
- {her} asks the wrong-guess and the viewer's real question; ARUN answers and stays the expert;
- roles never alternate: she asks, he answers;
- every specific number, percentage, or rupee amount appears in the thesis/story seed above - anything unsourced is rephrased qualitatively, never fabricated;
- ARUN names the concept plainly ("In economics, this is called X") and bridges to the REAL named company/product from the sourced story;
- the final scene lands ARUN's ONE flat takeaway sentence (max 12 words), then one CTA line woven into the conversation voice;
- every visual_prompt is one illustrated story beat in the modern apartment world (office desk, steel tumbler, modern city skyline, no religious or regional props), no text/words inside images, each prompt unique."""
    elif _story_mode():
        user_prompt = f"""Core financial thesis: "{thesis}"
{seed_context}{forbidden_block}
Now generate the complete {target_scenes}-scene Arun story script (60-85 seconds, 150-200 words total, hard max 90) as JSON.
Remember: exactly {target_scenes} scenes.

Before answering, internally check that:
- the title is a curiosity story title (max 55 chars), no fear words, no #Shorts;
- {scene1_hook_instruction};
- the narration tells ONE continuous spoken story about Arun in third person, with varied sentence lengths;
- every specific number, percentage, or rupee amount in the narration appears in the thesis/story seed above - anything unsourced is rephrased qualitatively, never fabricated;
- the money-chain scenes carry exact sourced figures at every beat;
- one scene names the concept plainly ("In economics, this is called X");
- one scene bridges to today naming the REAL company/product from the sourced story;
- the final scene lands ONE flat takeaway sentence (max 12 words), then one short CTA line woven into the story voice;
- every visual_prompt is one illustrated story beat with Arun (young Indian man, 24, olive hoodie, modern city apartment with an office desk), no text/words inside images, each prompt unique."""
    else:
        user_prompt = f"""Core financial thesis: "{thesis}"
{seed_context}{forbidden_block}
Now generate the complete {target_scenes}-scene Fast-Hook cinematic short-story script (< 30s runtime, 55-75 words total) as JSON.
Remember: Exactly {target_scenes} scenes.

Before answering, internally check that:
- the title has no #Shorts tag and is max 50 chars;
- {scene1_hook_instruction};
- the narrations tell a single, continuous, suspenseful spoken story with natural connective flow ("and", "so", "until", "because", "that's when"), NEVER a list of facts;
- every specific number, percentage, or rupee amount in the narration appears in the thesis/story seed above - anything unsourced is rephrased qualitatively, with no fabricated figure and no unnamed "reports/studies/trackers" citation;
- scene 1 visual_prompt opens cold on dramatic evidence (crashing red candlestick chart, trading screen, bank alert);
- scenes 2-{target_scenes-1} describe contextual B-roll objects, screens, or documents with broll_keyword (NO people);
- scene {target_scenes} delivers the sharp takeaway rule and spoken CTA;
- the total narration across all {target_scenes} scenes is 56–80 words (target 22–26 seconds runtime; a fresh visual every ~3 seconds)."""

    for model in _MODELS_PRIORITY:
        log.info("Trying model (Gemma/Gemini API): %s", model)
        try:
            raw = _call_model(user_prompt, model)
            log.debug("Raw response: %d chars", len(raw))

            data = None
            try:
                data = _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as parse_error:
                log.warning("Model returned malformed JSON; requesting repair pass: %s", parse_error)
                try:
                    repaired_raw = _repair_json(raw, parse_error, model, target_scenes=target_scenes)
                    data = _extract_json(repaired_raw)
                except Exception as repair_err:
                    log.warning("JSON repair pass failed: %s", repair_err)
                    continue

            if not data:
                continue

            try:
                script = ScriptPayload(**data)
            except Exception as val_error:
                log.warning("Script failed validation (%s); attempting repair pass", val_error)
                try:
                    repaired_raw = _repair_json(raw, val_error, model, target_scenes=target_scenes)
                    data = _extract_json(repaired_raw)
                    script = ScriptPayload(**data)
                except Exception as val_repair_err:
                    log.warning("Validation repair pass failed: %s; invoking Script Doctor deterministic repair...", val_repair_err)
                    try:
                        from src.agents.rewriter_agent import EnglishScriptRewriterAgent
                        repaired_data = EnglishScriptRewriterAgent().auto_repair_script(data, failure_reason=str(val_error), topic=thesis)
                        script = ScriptPayload(**repaired_data)
                        log.info("✓ Script successfully rescued by Script Doctor deterministic auto-repair.")
                    except Exception as auto_err:
                        log.warning("Script Doctor deterministic rescue failed: %s", auto_err)
                        continue

            total_words = sum(len(s.narration.split()) for s in script.scenes)
            log.info(
                "✓ Script ready | model: %s | title: '%s' | total_words: %d",
                model, script.title, total_words,
            )
            return script

        except Exception as exc:
            log.warning("Model %s failed: %s — trying next", model, exc)
            continue

    raise RuntimeError(
        f"All AI API models ({_MODELS_PRIORITY}) failed to produce a valid 6-scene Fast-Hook script for: '{thesis}'. "
        "Static fallback templates have been permanently deleted per user configuration."
    )

def script_to_dict(script: ScriptPayload) -> dict:
    return script.model_dump()
