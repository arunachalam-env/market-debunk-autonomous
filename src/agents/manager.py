"""
src/agents/manager.py

The main orchestrator for the market-debunk-autonomous pipeline.
Executes the full daily workflow:
  1. Discovery & Thesis
  2. Dedup Gate
  3. Script Generation
  4. Voice Synthesis
  5. Visual Sourcing
  6. FFmpeg Assembly (Subtitles + BGM)
  7. Logging & Notifications
  8. YouTube Upload (optional)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from src.utils.config import settings
from src.utils.logger import get_logger, PhaseTimer
from src.utils.master_package import export_master_package

# Import agents
from src.agents import topic_agent, script_agent, voice_agent, visual_agent, evaluator, quality_gate
from src.agents.rewriter_agent import EnglishScriptRewriterAgent
from src.agents.distribution_seo_agent import DistributionSEOAgent
from src.rendering import subtitles, assembler
from src.publishing import youtube_uploader, telegram_notifier, instagram_publisher, facebook_publisher

log = get_logger(__name__, phase="orchestrator")


def run_pipeline():
    """Execute the full autonomous video generation pipeline."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = settings.OUTPUT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    audio_dir = run_dir / "audio"
    visuals_dir = run_dir / "visuals"
    
    log.info("==================================================")
    log.info("🚀 STARTING PIPELINE RUN: %s", run_id)
    log.info("==================================================")
    
    total_start = time.time()
    stats = {}

    # ── Phase 0: Analytics Sensor, Performance Tuner & Timing Guard ──
    from src.analytics.analytics_sensor import AnalyticsSensor
    from src.analytics.tuner_agent import PerformanceTuningAgent
    from src.timing.timing_guard import TimingGuard

    sensor = AnalyticsSensor()
    try:
        audit_res = sensor.audit_recent_posts()
        log.info("✓ 48h Analytics sensor run: %s", audit_res)
    except Exception as audit_err:
        log.warning("Analytics audit skipped (%s)", audit_err)

    tuner = PerformanceTuningAgent()
    try:
        playbook = tuner.generate_playbook()
        log.info(
            "✓ Performance Tuning Playbook ready: optimal runtime %.1fs, %d target words",
            playbook.get("optimal_runtime_seconds", 24.0),
            playbook.get("optimal_word_count", 62),
        )
    except Exception as tune_err:
        log.warning("Performance tuning pass skipped (%s)", tune_err)

    timing_guard = TimingGuard()
    can_proceed, hours_elapsed = timing_guard.check_cooldown(min_hours=3.0)
    if not can_proceed:
        log.warning("🛑 Cooldown active (%.1f h elapsed < 3.0h min). Exiting pipeline to protect feed reach.", hours_elapsed)
        sys.exit(0)

    timing_guard.apply_jitter(min_seconds=5, max_seconds=20)

    try:
        # ── Phase 1: Topic Discovery (Battle-Tested Real-World Sourcing) ──
        # Scans 7 Indian YouTube channels (Money Pechu, PR Sundar, etc.) + 24h Google News via SerpApi
        with PhaseTimer("Phase 1: Topic Discovery"):
            topic_data = topic_agent.discover_topic()
            channel = topic_data["channel"]
            video_id = topic_data["video_id"]
            thesis = topic_data["thesis"]
            story_seed = topic_data.get("story_seed", {})
            log.info("Chosen channel: %s", channel)
            log.info("Core thesis: %s", thesis)
            log.info("Story seed concept: %s", story_seed.get("concept_name", "N/A"))

        # ── Phase 1.2: Autonomous Channel Director LLM Strategic Layer ────
        # Operates directly ON TOP OF the freshly sourced real-world story
        director = None
        strategic_brief = None
        try:
            from src.agents.channel_director import ChannelDirectorAgent
            director = ChannelDirectorAgent()
            strategic_brief = director.formulate_strategic_brief(topic_data=topic_data)
            if strategic_brief and strategic_brief.topic_thesis:
                # Elevate thesis with the Director's cynical framing while keeping real-world source_id
                thesis = strategic_brief.topic_thesis
                topic_data["thesis"] = thesis
                log.info("✓ Channel Director Strategic Brief elevated topic: '%s'", thesis)
                log.info("Strategic Angle: %s", strategic_brief.strategic_angle)
        except Exception as dir_err:
            log.warning("Director Agent brief notice (%s); proceeding with raw sourced topic", dir_err)
            strategic_brief = None

        # ── Phase 1.5: Dedup Gate ─────────────────────────────────────────
        with PhaseTimer("Phase 1.5: Dedup Gate"):
            is_dup, score, match = evaluator.is_duplicate(thesis)
            if is_dup:
                log.warning("🛑 Topic is too similar to '%s' (score %.2f). Switching to fresh evergreen seed...", match, score)
                from src.agents.topic_agent import _EVERGREEN_TOPICS, summarize_to_story_seed
                found_fresh = False
                for eg in _EVERGREEN_TOPICS:
                    eg_dup, _, _ = evaluator.is_duplicate(eg)
                    if not eg_dup:
                        thesis = eg
                        channel = "Market Debunk Research"
                        seed_data = summarize_to_story_seed(f"FINANCIAL CONCEPT: {eg}", eg)
                        story_seed = seed_data.get("story_seed", {})
                        log.info("✓ Switched to fresh evergreen topic: '%s'", thesis)
                        found_fresh = True
                        break
                if not found_fresh:
                    log.warning("All evergreen topics duplicate recent history. Halting to prevent feed spam.")
                    sys.exit(0)
            log.info("Topic passed uniqueness check.")

        # ── Phase 2: Script Generation (with Script Doctor) ───────────────
        with PhaseTimer("Phase 2: Script Generation"):
            rewriter = EnglishScriptRewriterAgent()

            # ── Phase 1.75: Question Crafting (Anti-Repetition Hook) ──────
            question_hook = ""
            try:
                from src.agents.question_agent import QuestionCraftingAgent
                hook_type = getattr(strategic_brief, "hook_type", "LOSS_IMPLICATION") if strategic_brief else "LOSS_IMPLICATION"
                topic_keywords = story_seed.get("concept_name", "") if isinstance(story_seed, dict) else ""
                question_hook = QuestionCraftingAgent().craft_question(
                    thesis=thesis,
                    hook_type=hook_type,
                    topic_keywords=topic_keywords,
                )
                log.info("✓ QuestionCraftingAgent: Hook crafted → '%s'", question_hook)
            except Exception as q_err:
                log.warning(
                    "QuestionCraftingAgent notice (%s); rewriter + Pydantic validator will enforce question format as fallback.",
                    q_err,
                )

            script = script_agent.generate_script(
                thesis, channel, story_seed=story_seed, question_hook=question_hook
            )
            script_dict = script_agent.script_to_dict(script)

            # Engage English Script Doctor to guarantee 5-7 word hook, eliminate citations,
            # enforce unique visual prompts, and attach seamless curiosity loop connector.
            script_dict = rewriter.auto_repair_script(script_dict, topic=thesis)

            # ── Phase 2.5: Pre-publication Fact-Check Gate ─────────────
            # Nothing reaches TTS, rendering, or any platform with unverified
            # claims. Fail-closed: an unrunnable check also halts the run.
            if settings.FACT_CHECK_ENABLED:
                from src.agents.fact_check_agent import FactCheckAgent
                fc_agent = FactCheckAgent()
                fc_result = fc_agent.check_script(script_dict, thesis=thesis, source_excerpt=str(story_seed.get("source_excerpt", "")))

                # One regeneration attempt: feed the blocked claims back to the
                # writer so it can drop/rephrase them, then re-run the gate.
                # The gate itself is never weakened - a second failure still halts.
                if not fc_result.passed and fc_result.check_ran and fc_result.blocking_claims:
                    forbidden = [
                        str(c.get("claim", "")).strip()
                        for c in fc_result.blocking_claims
                        if str(c.get("claim", "")).strip()
                    ]
                    if forbidden:
                        log.warning(
                            "Fact-check blocked the draft; regenerating the script once without %d failed claim(s)...",
                            len(forbidden),
                        )
                        script = script_agent.generate_script(
                            thesis,
                            channel,
                            story_seed=story_seed,
                            question_hook="",
                            forbidden_claims=forbidden,
                        )
                        script_dict = script_agent.script_to_dict(script)
                        script_dict = rewriter.auto_repair_script(script_dict, topic=thesis)
                        fc_result = fc_agent.check_script(script_dict, thesis=thesis, source_excerpt=str(story_seed.get("source_excerpt", "")))

                if not fc_result.passed:
                    blocking = fc_result.check_ran or settings.FACT_CHECK_REQUIRED
                    if blocking:
                        log.warning("🛑 FACT-CHECK GATE: halting run before any publishing. %s", fc_result.summary())
                        if settings.ENABLE_TELEGRAM:
                            try:
                                telegram_notifier.send_completion_notification(
                                    title=script_dict.get("title", "(untitled)"),
                                    thesis=thesis,
                                    custom_message=(
                                        "🛑 Today's Short was blocked by the fact-check gate.\n\n"
                                        + fc_result.summary()[:700]
                                    ),
                                )
                            except Exception as tg_err:
                                log.warning("Telegram fact-check notice failed: %s", tg_err)
                        sys.exit(0)
                    log.warning("Fact-check failed but FACT_CHECK_REQUIRED=false; continuing unchecked.")

            is_dup, score, match = evaluator.is_duplicate(script_dict["title"], threshold=0.78)
            if is_dup:
                log.warning("Generated title duplicates '%s' (similarity %.2f). Auto-correcting title angle...", match, score)
                from src.utils.youtube_titles import format_high_reach_title, resolve_high_reach_keyword
                concept = story_seed.get("concept", "") if isinstance(story_seed, dict) else ""
                clean_title = script_dict["title"].replace("#Shorts", "").strip(" :|-")
                keyword = resolve_high_reach_keyword(f"{thesis} {clean_title} {concept}")
                # Remove keyword from clean_title if it starts with it
                if clean_title.lower().startswith(keyword.lower()):
                    clean_title = clean_title[len(keyword):].strip(" :|-")
                script_dict["title"] = format_high_reach_title(keyword, f"Exposing {clean_title}", max_length=55)
                log.info("✓ Auto-corrected title to: '%s'", script_dict["title"])

            # Preflight timing before any TTS or visual generation.
            estimated_seconds = sum(
                len(scene.get("narration", "").split())
                for scene in script_dict["scenes"]
            ) / 2.3
            # The voice agent has automatic atempo clamping (15.0s - 42.0s),
            # so allow a safe window and let voice_agent clamp rather than failing early.
            if not 14 <= estimated_seconds <= 46:
                log.warning("Estimated duration %.1fs outside ideal window; voice agent will apply atempo clamping", estimated_seconds)
            
            # Save script to output for debugging
            script_path = run_dir / "script.json"
            import json
            script_path.write_text(json.dumps(script_dict, indent=2), encoding="utf-8")


        # ── Phase 3: Voice Synthesis ──────────────────────────────────────
        with PhaseTimer("Phase 3: Voice Synthesis"):
            voice_results = voice_agent.synthesize_all_scenes(script_dict["scenes"], audio_dir)
            stats["total_duration"] = sum(r["duration"] for r in voice_results)
            quality_gate.validate_duration(stats["total_duration"])

        # ── Phase 4: Visual Sourcing ──────────────────────────────────────
        with PhaseTimer("Phase 4: Visual Sourcing"):
            visual_results = visual_agent.source_all_visuals(
                script_dict["scenes"], visuals_dir, story_seed=story_seed
            )
            stats["visual_sources"] = ", ".join(set(r["source"] for r in visual_results))
            quality_gate.validate_visual_assets(
                visual_results,
                {scene["scene_id"] for scene in script_dict["scenes"]},
            )
            master_package = export_master_package(
                run_dir,
                thesis,
                script_dict,
                visual_results,
                source_id=topic_data.get("source_id", ""),
                strategic_brief=strategic_brief.model_dump() if strategic_brief else None,
                source_excerpt=str(story_seed.get("source_excerpt", "")),
            )
            log.info("Exported Tamil companion visual package: %s", master_package)

        # ── Phase 5: FFmpeg Assembly ──────────────────────────────────────
        with PhaseTimer("Phase 5: Video Assembly"):
            import random
            
            ass_path = run_dir / "subtitles.ass"
            subtitles.generate_ass_file(voice_results, ass_path, hook_title=script_dict.get("title", ""))
            
            # Select random BGM track from the premium folder
            bgm_dir = Path("assets/bgm")
            bgm_path = None
            if bgm_dir.exists():
                tracks = [
                    track for track in bgm_dir.glob("*.mp3")
                    if track.stat().st_size >= settings.BGM_MIN_BYTES
                ]
                if tracks:
                    bgm_path = random.choice(tracks)
                    log.info(f"Selected BGM track: {bgm_path.name}")
            
            final_video = assembler.assemble_video(
                voice_results=voice_results,
                visual_results=visual_results,
                ass_path=ass_path,
                run_dir=run_dir,
                bgm_path=bgm_path,
            )
            quality_gate.validate_rendered_video(final_video)

        # ── Phase 6: Post-Processing & Recording ──────────────────────────
        with PhaseTimer("Phase 6: Logging & Record keeping"):
            evaluator.record_topic(thesis)
            evaluator.record_title(script_dict["title"])
            evaluator.record_source_video(topic_data.get("video_id", ""))
            evaluator.record_source_id(topic_data.get("source_id", ""))
            log.info("Recorded topic to prevent future duplicates.")

        # ── Phase 6.5: SEO & Distribution Engineering ─────────────────────
        with PhaseTimer("Phase 6.5: SEO & Distribution Engineering"):
            from src.agents.distribution_seo_agent import DistributionSEOAgent
            seo_agent = DistributionSEOAgent()
            dist_pkg = seo_agent.generate_package(
                thesis=thesis,
                script_dict=script_dict,
                topic_data=topic_data,
            )
            log.info("✓ Multi-platform SEO Distribution Package generated successfully.")
            # Run 3 SEO Super Subagents: YouTube → Instagram → Facebook
            dist_pkg = seo_agent.post_process(dist_pkg, thesis, script_dict)
            log.info("✓ RapidAPI SEO enhancement pass complete.")

        # ── Phase 7: Publishing ───────────────────────────────────────────
        with PhaseTimer("Phase 7: Publishing"):
            yt_url = None
            yt_id = None
            if settings.ENABLE_YT_UPLOAD:
                yt_id = youtube_uploader.upload_video(
                    video_path=final_video,
                    title=dist_pkg.youtube.title,
                    description=dist_pkg.get_youtube_description(),
                    hashtags=dist_pkg.youtube.hashtags,
                )
                if yt_id:
                    yt_url = f"https://www.youtube.com/shorts/{yt_id}"

            ig_url = None
            ig_id = None
            if settings.ENABLE_INSTAGRAM:
                ig_url = instagram_publisher.publish_reel(
                    video_path=final_video,
                    title=dist_pkg.instagram.first_line_hook,
                    description=f"{dist_pkg.instagram.body_copy}\n\n{dist_pkg.instagram.comment_trigger}\n\n{dist_pkg.instagram.share_save_cta}",
                    hashtags=dist_pkg.instagram.hashtags,
                )
                if ig_url:
                    ig_id = getattr(ig_url, "media_id", None) or ig_url.rstrip("/").split("/")[-1]

            fb_url = None
            fb_id = None
            fb_page = getattr(settings, "FACEBOOK_PAGE_ID", "").strip() or getattr(settings, "FB_PAGE_ID", "").strip()
            if fb_page:
                fb_url = facebook_publisher.publish_reel(
                    video_path=final_video,
                    title=dist_pkg.facebook.story_hook,
                    description=f"{dist_pkg.facebook.narrative_body}\n\n{dist_pkg.facebook.discussion_question}",
                    hashtags=dist_pkg.facebook.topic_tags,
                )
                if fb_url:
                    fb_id = fb_url.rstrip("/").split("/")[-1]


            if settings.ENABLE_TELEGRAM:
                telegram_notifier.send_completion_notification(
                    title=dist_pkg.youtube.title,
                    thesis=thesis,
                    youtube_url=yt_url,
                    instagram_url=ig_url,
                    facebook_url=fb_url,
                    video_path=final_video,
                    run_stats=stats,
                )

            # Record to publish_ledger.json for cooldown and 48h analytics
            try:
                timing_guard.record_publish(
                    title=dist_pkg.youtube.title,
                    topic=thesis,
                    platform_urls={"youtube": yt_url, "instagram": ig_url, "facebook": fb_url},
                    platform_ids={"youtube": yt_id, "instagram": ig_id, "facebook": fb_id},
                    hashtags=dist_pkg.instagram.hashtags,
                    hook=dist_pkg.instagram.first_line_hook,
                    duration_seconds=float(stats.get("total_duration", 25.0)),
                )
            except Exception as rec_err:
                log.warning("Failed to record publication to ledger: %s", rec_err)

            # Autonomous Community Engagement (Channel Director)
            if director and strategic_brief:
                try:
                    director.execute_post_publish(
                        youtube_id=yt_id,
                        instagram_id=ig_id,
                        brief=strategic_brief,
                    )
                except Exception as comm_err:
                    log.warning("Post-publish engagement step skipped non-fatally: %s", comm_err)

        total_time = time.time() - total_start
        log.info("==================================================")
        log.info("✅ PIPELINE COMPLETED SUCCESSFULLY in %.1fs", total_time)
        log.info("Output Video: %s", final_video.resolve())
        log.info("==================================================")

    except SystemExit:
        log.info("Pipeline halted normally.")
    except Exception as exc:
        log.exception("❌ PIPELINE FAILED FATALLY: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
