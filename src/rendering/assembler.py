"""
src/rendering/assembler.py

Phase 5 — FFmpeg Video Assembly

Takes per-scene audio + visual assets and assembles the final
distribution-ready YouTube Short (1080×1920, 30fps, H.264/AAC).

Pipeline:
  1. For each scene:
     a. If asset is a VIDEO: crop/scale to 1080×1920, loop to match audio duration
     b. If asset is an IMAGE: convert to video (ken-burns pan effect), match audio duration
     c. Merge scene video + scene audio → scene_clip.mp4
  2. Concatenate all scene clips → raw_video.mp4
  3. Burn .ass subtitles into raw_video → subtitled_video.mp4
  4. Mix BGM audibly under voice with sidechain ducking → final.mp4
  5. Rename to distribution_ready.mp4
"""
from __future__ import annotations

import subprocess
import sys
import shutil
import time
import wave
from pathlib import Path
from typing import Optional

from src.utils.config import settings
from src.utils.logger import get_logger

log = get_logger(__name__, phase="video_assembly")

# ──────────────────────────────────────────────────────────────────────────────
#  FFmpeg Helper
# ──────────────────────────────────────────────────────────────────────────────

def _ffmpeg(*args: str, description: str = "") -> None:
    """Run an ffmpeg command; raise on non-zero exit."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    log.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg FAILED (%s):\n  stderr: %s", description, result.stderr[:500])
        raise RuntimeError(f"ffmpeg error in '{description}': {result.stderr[-300:]}")
    if description:
        log.info("✓ %s", description)


def get_audio_duration(path: Path) -> float:
    """Returns duration in seconds using wave or ffprobe with safe fallback."""
    try:
        if str(path).endswith(".wav") and Path(path).is_file():
            with wave.open(str(path), "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10
        )
        return float(res.stdout.strip())
    except Exception as exc:
        log.warning("ffprobe failed on %s (%s); defaulting to 45.0s", path, exc)
        return 45.0


# ──────────────────────────────────────────────────────────────────────────────
#  Step 1 — Per-Scene Clip Builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_scene_from_video(
    video_path: Path,
    duration: float,
    output_path: Path,
) -> None:
    """
    Crop and loop a stock video to match duration, scale to 1080×1920.
    Builds a pure video clip without audio so voice track remains continuous.
    """
    w, h = settings.VIDEO_WIDTH, settings.VIDEO_HEIGHT
    fps = settings.VIDEO_FPS

    # Crop rather than pad: empty bars are a release-quality failure for Shorts.
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},"
        f"setsar=1,"
        f"zoompan=z='min(zoom+0.0005,1.04)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*fps)}:s={w}x{h}:fps={fps}"
    )

    _ffmpeg(
        "-stream_loop", "-1",          # loop video if shorter than duration
        "-i", str(video_path),
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-threads", "4",
        "-crf", "23",
        "-an",
        str(output_path),
        description=f"build scene clip from video: {output_path.name}"
    )


def _build_scene_from_image(
    image_path: Path,
    duration: float,
    output_path: Path,
    scene_id: int = 1,
    is_avatar: bool = False,
) -> None:
    """
    Scale image to fill (no black bars), crop to 1080x1920, and apply varied ken-burns zoom.
    If is_avatar is True (Scene 1 Hook / Outro), adds animated speech mouth/jaw movement.
    Builds a pure video clip without audio so voice track remains continuous.
    """
    w, h = settings.VIDEO_WIDTH, settings.VIDEO_HEIGHT
    fps = settings.VIDEO_FPS
    n_frames = int(duration * fps)

    # Vary the zoom direction based on scene_id to prevent repetitive motion
    pan_type = scene_id % 3
    if pan_type == 0:
        zp_motion = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    elif pan_type == 1:
        zp_motion = "x='iw/2-(iw/zoom/2)+2':y='ih/2-(ih/zoom/2)'"
    else:
        zp_motion = "x='iw/2-(iw/zoom/2)-2':y='ih/2-(ih/zoom/2)'"

    if is_avatar:
        # Talking mouth movement animation synchronized with speech cadence (4.2 Hz)
        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
            f"zoompan=z='min(zoom+0.0006,1.04)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={w}x{h}:fps={fps},split=2[base1][base2];"
            f"[base2]crop=w=240:h=140:x=420:y=1080[jaw];"
            f"[base1][jaw]overlay=x=420:y='1080 + 6*max(0, sin(2*PI*t*4.2))':eval=frame[vout]"
        )
        _ffmpeg(
            "-loop", "1",
            "-framerate", str(fps),
            "-i", str(image_path),
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-threads", "4",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
            description=f"build animated speaking avatar clip: {output_path.name}"
        )
    else:
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase," # Fill frame, no black bars
            f"crop={w}:{h},"
            f"setsar=1,"
            f"zoompan=z='min(zoom+0.0008,1.05)':{zp_motion}:d={n_frames}:s={w}x{h}:fps={fps}"
        )
        _ffmpeg(
            "-loop", "1",
            "-framerate", str(fps),
            "-i", str(image_path),
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-threads", "4",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
            description=f"build scene clip from image: {output_path.name}"
        )


def build_scene_clips(
    voice_results: list[dict],
    visual_results: list[dict],
    clips_dir: Path,
) -> list[Path]:
    """
    Build a video-only clip for each scene (video or image → looped/animated video).
    Each clip duration matches exactly the voice narration duration.
    Returns list of clip paths in scene order.
    """
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []

    # Index by scene_id for easy lookup
    voice_map = {r["scene_id"]: r for r in voice_results}
    visual_map = {r["scene_id"]: r for r in visual_results}
    sorted_sids = sorted(voice_map.keys())

    for idx, scene_id in enumerate(sorted_sids):
        voice = voice_map[scene_id]
        visual = visual_map[scene_id]

        # Clip duration = exact voice duration. No padding. Audio is master.
        duration = voice["duration"]

        asset_path = Path(visual["asset_path"])
        asset_type = visual["asset_type"]

        clip_path = clips_dir / f"scene_{scene_id}_clip.mp4"

        log.info(
            "Building scene %d clip | type: %s | duration: %.2fs",
            scene_id, asset_type, duration,
        )

        if asset_type == "video":
            _build_scene_from_video(asset_path, duration, clip_path)
        else:
            is_avatar = visual.get("source") == "presenter_avatar" or "presenter" in asset_path.name.lower()
            _build_scene_from_image(asset_path, duration, clip_path, scene_id=scene_id, is_avatar=is_avatar)

        clip_paths.append(clip_path)

    return clip_paths



# ──────────────────────────────────────────────────────────────────────────────
#  Step 2 — Concatenation & Master Voice Track
# ──────────────────────────────────────────────────────────────────────────────

def concatenate_voice_audio(voice_results: list[dict], output_path: Path) -> Path:
    """
    Concatenate all scene voice tracks into a single continuous 48kHz stereo WAV track.

    IMPORTANT: Each MP3 is first decoded to a raw 48kHz PCM WAV individually.
    This eliminates the LAME encoder delay (~576-1152 samples) that causes
    ~26ms micro-gaps at every scene boundary when MP3 files are concatenated directly.
    """
    wav_dir = output_path.parent / "voice_wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)

    sorted_results = sorted(voice_results, key=lambda r: r.get("scene_id", 0))
    wav_paths: list[Path] = []
    for r in sorted_results:
        mp3_path = Path(r["mp3_path"]).resolve()
        wav_path = wav_dir / f"{mp3_path.stem}.wav"
        # Decode MP3 to raw 48kHz stereo PCM WAV — no encoder delay artifacts
        _ffmpeg(
            "-i", str(mp3_path),
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(wav_path),
            description=f"decode scene audio to WAV: {wav_path.name}"
        )
        wav_paths.append(wav_path)

    concat_list = output_path.parent / "voice_concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for wav_path in wav_paths:
            f.write(f"file '{wav_path.resolve().as_posix()}'\n")

    _ffmpeg(
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-ar", "48000",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(output_path),
        description="concatenate decoded WAV voice tracks into master voice"
    )
    return output_path


def _load_pcm_stereo_48k(file_path: Path, temp_dir: Path, target_volume: float = 1.0) -> Optional[bytes]:
    """Helper to load 48kHz stereo 16-bit PCM bytes from WAV or other audio formats."""
    if not file_path.is_file():
        return None
    
    # If already a 48kHz stereo 16-bit WAV and no volume change, read directly
    if file_path.suffix.lower() == ".wav" and target_volume == 1.0:
        try:
            with wave.open(str(file_path), "rb") as wf:
                if wf.getnchannels() == 2 and wf.getsampwidth() == 2 and wf.getframerate() == 48000:
                    return wf.readframes(wf.getnframes())
        except Exception:
            pass

    # Otherwise decode via FFmpeg
    temp_wav = temp_dir / f"decode_{file_path.stem}_{int(target_volume*100)}.wav"
    try:
        vol_filter = f"volume={target_volume:.2f}" if target_volume != 1.0 else "anull"
        _ffmpeg(
            "-i", str(file_path),
            "-af", vol_filter,
            "-ar", "48000",
            "-ac", "2",
            "-c:a", "pcm_s16le",
            str(temp_wav),
            description=f"decode {file_path.name} to 48kHz PCM"
        )
        with wave.open(str(temp_wav), "rb") as wf:
            return wf.readframes(wf.getnframes())
    except Exception as exc:
        log.warning("Could not decode audio asset %s: %s", file_path.name, exc)
        return None
    finally:
        if temp_wav.exists():
            try:
                temp_wav.unlink()
            except Exception:
                pass


def _mix_pcm_into_buffer(
    buffer: bytearray,
    sfx_bytes: bytes,
    start_sec: float,
    bytes_per_sec: int = 192000,
    gain: float = 1.0,
) -> None:
    """Mixes 48kHz 16-bit stereo PCM bytes into buffer at start_sec with sample-accurate alignment."""
    if not sfx_bytes or start_sec < 0:
        return
    offset = int(start_sec * bytes_per_sec)
    offset = (offset // 4) * 4  # Align to 4-byte stereo 16-bit sample boundary
    max_len = min(len(sfx_bytes), len(buffer) - offset)
    for j in range(0, max_len, 2):
        orig_val = int.from_bytes(buffer[offset + j : offset + j + 2], "little", signed=True)
        sfx_val = int.from_bytes(sfx_bytes[j : j + 2], "little", signed=True)
        if gain != 1.0:
            sfx_val = int(sfx_val * gain)
        mixed_val = max(-32768, min(32767, orig_val + sfx_val))
        buffer[offset + j : offset + j + 2] = mixed_val.to_bytes(2, "little", signed=True)


def build_sfx_track(
    voice_results: list[dict],
    total_duration: float,
    output_path: Path,
) -> Optional[Path]:
    """
    Build a continuous 48kHz stereo WAV master SFX track with high-impact sound design:
    1. Scene 1 (0.0s): Cinematic sub-bass impact hit timed with warning hook banner.
    2. Scene Cuts: Crisp, punchy transition whooshes leading cuts by 80ms.
    3. Scene 3/4 Reveal: Sharp metallic chime / cash ding for trap revelation.
    4. Final Scene: Snappy UI pop for the spoken CTA.
    """
    sfx_dir = settings.ASSETS_DIR / "audio" / "sfx"
    audio_dir = settings.ASSETS_DIR / "audio"
    temp_dir = output_path.parent

    # Find SFX assets with multiple fallbacks (checking user-provided opening SFX first)
    local_opening_dir = Path(r"D:\downloads\1 downloaded\a1\opening")
    local_sfx_dir = Path(r"D:\downloads\1 downloaded\sfx")
    opening_candidates = [
        sfx_dir / "mixkit-ui-zoom-in-long-sound-2621.wav",
        local_opening_dir / "mixkit-ui-zoom-in-long-sound-2621.wav",
        sfx_dir / "opening_sfx.wav",
        sfx_dir / "opening_hit.wav",
        sfx_dir / "impact_hit.wav",
        audio_dir / "opening_sfx.wav",
    ]
    opening_src = next((p for p in opening_candidates if p.is_file()), None)

    whoosh_candidates = [
        sfx_dir / "whoosh.mp3",
        audio_dir / "whoosh.mp3",
        sfx_dir / "sfx_whoosh.webm",
        audio_dir / "sfx_whoosh.webm",
    ]
    whoosh_src = next((p for p in whoosh_candidates if p.is_file()), None)

    ding_src = sfx_dir / "accent_ding.wav"
    if not ding_src.is_file():
        ding_src = audio_dir / "accent_ding.wav"

    pop_src = sfx_dir / "pop_accent.wav"
    if not pop_src.is_file():
        pop_src = audio_dir / "pop_accent.wav"

    # Load audio buffers (calibrated so speech remains dominant and crisp)
    opening_bytes = _load_pcm_stereo_48k(opening_src, temp_dir, target_volume=1.0) if opening_src else None
    whoosh_bytes = _load_pcm_stereo_48k(whoosh_src, temp_dir, target_volume=0.45) if whoosh_src else None
    ding_bytes = _load_pcm_stereo_48k(ding_src, temp_dir, target_volume=0.50) if ding_src.is_file() else None
    pop_bytes = _load_pcm_stereo_48k(pop_src, temp_dir, target_volume=0.45) if pop_src.is_file() else None

    if not any([opening_bytes, whoosh_bytes, ding_bytes, pop_bytes]):
        log.warning("No SFX assets found in %s or %s — skipping SFX track", sfx_dir, audio_dir)
        return None

    try:
        bytes_per_sec = 48000 * 2 * 2  # 192,000 bytes/sec
        total_bytes = int(total_duration * bytes_per_sec) + bytes_per_sec
        sfx_buffer = bytearray(total_bytes)

        # 1. Scene 1 Hook Opening SFX at t = 0.0s
        if opening_bytes:
            _mix_pcm_into_buffer(sfx_buffer, opening_bytes, start_sec=0.0, bytes_per_sec=bytes_per_sec)
            log.info("  ↳ SFX: Added Scene 1 hook opening SFX at 0.00s")

        sorted_voices = sorted(voice_results, key=lambda r: r["scene_id"])
        num_scenes = len(sorted_voices)
        current_time = 0.0

        for i, voice in enumerate(sorted_voices):
            dur = voice["duration"]

            # 2. Transition Whoosh at each scene cut (leading cut by 80ms)
            if i > 0 and whoosh_bytes:
                cut_time = max(0.0, current_time - 0.08)
                _mix_pcm_into_buffer(sfx_buffer, whoosh_bytes, start_sec=cut_time, bytes_per_sec=bytes_per_sec)

            # 3. Core Revelation / Math Trap Accent (Scene 3 or Scene 4)
            reveal_scene_idx = 2 if num_scenes >= 4 else 1
            if i == reveal_scene_idx and ding_bytes:
                ding_time = current_time + 0.12
                _mix_pcm_into_buffer(sfx_buffer, ding_bytes, start_sec=ding_time, bytes_per_sec=bytes_per_sec)
                log.info("  ↳ SFX: Added revelation accent ding at %.2fs (Scene %d)", ding_time, i + 1)

            # 4. Final Scene CTA Pop Accent
            if i == num_scenes - 1 and num_scenes > 2 and pop_bytes:
                pop_time = current_time + 0.10
                _mix_pcm_into_buffer(sfx_buffer, pop_bytes, start_sec=pop_time, bytes_per_sec=bytes_per_sec)
                log.info("  ↳ SFX: Added CTA pop accent at %.2fs (Scene %d)", pop_time, i + 1)

            current_time += dur

        with wave.open(str(output_path), "wb") as out_wf:
            out_wf.setnchannels(2)
            out_wf.setsampwidth(2)
            out_wf.setframerate(48000)
            out_wf.writeframes(sfx_buffer)

        log.info(" ✓ Master high-impact SFX track built successfully: %s (%.2fs)", output_path.name, total_duration)
        return output_path
    except Exception as e:
        log.warning("Failed to build high-impact SFX track (%s) — proceeding without SFX", e)
        return None


def mux_video_and_audio(video_path: Path, audio_path: Path, output_path: Path) -> Path:
    """Mux concatenated video with continuous master voice audio at standard 48kHz AAC."""
    _ffmpeg(
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-shortest",
        str(output_path),
        description="mux continuous video and master voice track"
    )
    return output_path


def clean_concat_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """
    Concatenate all scene clips with a clean re-encode using FFmpeg filter_complex concat.

    Unlike stream-copy (-c copy), this re-encodes video so there are zero codec
    boundary artifacts, zero B-frame reference mismatches, and zero timing gaps.
    The filter_complex approach also handles clips with slightly different time-bases
    from the zoompan filter without any stutter or freeze at scene boundaries.
    """
    if len(clip_paths) == 1:
        _ffmpeg("-i", str(clip_paths[0]), "-c", "copy", str(output_path),
                description="single clip passthrough")
        return output_path

    log.info("Clean-concatenating %d scene clips with re-encode …", len(clip_paths))

    inputs = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    n = len(clip_paths)
    # filter_complex: label each input video stream, concat all, output [outv]
    input_labels = "".join(f"[{i}:v]" for i in range(n))
    vf_complex = f"{input_labels}concat=n={n}:v=1:a=0[outv]"

    _ffmpeg(
        *inputs,
        "-filter_complex", vf_complex,
        "-map", "[outv]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-threads", "4",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an",
        str(output_path),
        description=f"clean re-encode concat of {n} scene clips"
    )
    return output_path





def concatenate_clips(clip_paths: list[Path], output_path: Path) -> Path:
    """Fallback: Concatenate all scene clips via FFmpeg concat demuxer (stream copy)."""
    concat_list = output_path.parent / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for clip in clip_paths:
            f.write(f"file '{clip.resolve().as_posix()}'\n")

    _ffmpeg(
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(output_path),
        description="fallback concatenate all scene clips"
    )
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
#  Step 3 — Subtitle Burning
# ──────────────────────────────────────────────────────────────────────────────


def burn_subtitles(video_path: Path, ass_path: Path, output_path: Path) -> Path:
    """
    Hard-burn .ass subtitles into video frames.
    Must re-encode video for subtitle burn-in.
    Fallback chain: ass filter -> subtitles filter -> copy without subtitles.
    """
    ass_str = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:")

    try:
        _ffmpeg(
            "-i", str(video_path),
            "-vf", f"ass='{ass_str}'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-threads", "4",
            "-crf", "20",
            "-c:a", "copy",
            str(output_path),
            description="burn subtitles into video"
        )
        return output_path
    except Exception as exc:
        log.warning("FFmpeg ass filter failed (%s); trying subtitles filter fallback", exc)

    try:
        _ffmpeg(
            "-i", str(video_path),
            "-vf", f"subtitles='{ass_str}'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-threads", "4",
            "-crf", "20",
            "-c:a", "copy",
            str(output_path),
            description="burn subtitles via subtitles filter"
        )
        return output_path
    except Exception as exc2:
        log.warning("Subtitle burn failed entirely (%s); using raw video as fallback", exc2)
        shutil.copy2(video_path, output_path)
        return output_path


def overlay_brand_mark(video_path: Path, output_path: Path) -> Path:
    """Overlay the creator host badge or channel protection mark at the top-right corner."""
    creator_badge = settings.ASSETS_DIR / "creator_badge.png"
    if creator_badge.is_file():
        logo_path = creator_badge
    else:
        logo_path = settings.BRAND_MARK_PATH

    if not logo_path.is_file():
        log.warning("Brand protection / creator mark not found at %s — skipping overlay", logo_path)
        shutil.copy2(video_path, output_path)
        return output_path

    pad = settings.BRAND_MARK_PADDING
    # Delay brand mark until after the 2-second hook (t >= 3.5s) to eliminate opening friction
    vf = f"[1:v]scale={settings.BRAND_MARK_WIDTH}:-1[logo];[0:v][logo]overlay=W-w-{pad}:{pad}:enable='gte(t,3.5)'"
    try:
        _ffmpeg(
            "-i", str(video_path),
            "-i", str(logo_path),
            "-filter_complex", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-threads", "4",
            "-crf", "20",
            "-c:a", "copy",
            str(output_path),
            description="overlay brand protection mark"
        )
        return output_path
    except Exception as e:
        log.warning("Brand mark overlay failed (%s); falling back to unmodified video", e)
        shutil.copy2(video_path, output_path)
        return output_path


# ──────────────────────────────────────────────────────────────────────────────
#  Step 4 — BGM Mixing
# ──────────────────────────────────────────────────────────────────────────────

def mix_bgm(
    video_path: Path,
    bgm_path: Optional[Path],
    output_path: Path,
    bgm_volume_db: Optional[float] = None,
    use_ducking: bool = True,
) -> Path:
    """
    Layer BGM under the voiceover and normalise the mix.
    BGM is looped to match video duration and mixed at bgm_volume_db.
    The final mix is normalised to -14 LUFS.

    If bgm_path is None or doesn't exist, only applies loudness normalisation.
    """
    if bgm_volume_db is None:
        bgm_volume_db = settings.BGM_VOLUME_DB

    if not bgm_path or not bgm_path.exists():
        message = f"BGM track not found at {bgm_path}"
        if settings.BGM_MIX_REQUIRED:
            raise FileNotFoundError(message)
        log.warning("%s — applying voice-only loudness normalisation", message)
        _ffmpeg(
            "-i", str(video_path),
            "-af", "highpass=f=60,equalizer=f=2500:t=q:w=1.2:g=1.5,equalizer=f=6500:t=q:w=2.0:g=-1.5,compand=attacks=0.03:decays=0.15:points=-50/-50|-24/-16|0/-2:soft-knee=6,volume=1.0,loudnorm=I=-14:TP=-1.0:LRA=7",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
            description="loudness normalisation (no BGM)"
        )
        return output_path

    # Calculate BGM volume factor from dB
    vol_factor = 10 ** (bgm_volume_db / 20)

    # Mix: voice (boosted + presence EQ) + clearly audible BGM with gentle, rhythmic ducking under speech.
    voice_chain = (
        "highpass=f=70,"
        "equalizer=f=3000:t=q:w=1.2:g=2.2,"
        "equalizer=f=6500:t=q:w=2.0:g=-1.0,"
        "compand=attacks=0.03:decays=0.15:points=-50/-50|-24/-16|0/-2:soft-knee=6,"
        "volume=1.05"
    )

    if use_ducking:
        audio_filter = (
            f"[0:a]{voice_chain},"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asplit=2[voice_mix][voice_key];"
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={vol_factor:.4f}[bgm];"
            "[bgm][voice_key]sidechaincompress=threshold=0.18:ratio=2.5:attack=25:release=250[ducked];"
            "[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "loudnorm=I=-14:TP=-1.0:LRA=7[out]"
        )
        description = "BGM ducking mix + broadcast loudness normalisation (-14 LUFS)"
    else:
        audio_filter = (
            f"[0:a]{voice_chain},"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[voice];"
            f"[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume={vol_factor:.4f}[bgm];"
            "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
            "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "loudnorm=I=-14:TP=-1.0:LRA=7[out]"
        )
        description = "BGM simple mix + broadcast loudness normalisation (-14 LUFS)"

    _ffmpeg(
        "-i", str(video_path),
        "-stream_loop", "-1",
        "-i", str(bgm_path),
        "-filter_complex", audio_filter,
        "-map", "0:v",
        "-map", "[out]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        str(output_path),
        description=description
    )
    return output_path


def mix_bgm_with_retries(video_path: Path, bgm_path: Optional[Path], output_path: Path) -> Path:
    """
    Retry the preferred ducked BGM mix, then a simpler BGM mix. BGM remains the
    target output; voice-only finalization is reserved for the outer emergency
    fallback so the run still produces an inspectable artifact.
    """
    retry_count = max(settings.BGM_MIX_RETRIES, 1)
    last_exc: Optional[Exception] = None
    for attempt in range(1, retry_count + 1):
        try:
            log.info("BGM mix attempt %d/%d with ducking", attempt, retry_count)
            return mix_bgm(video_path, bgm_path, output_path, use_ducking=True)
        except Exception as exc:
            last_exc = exc
            log.warning("BGM ducking mix attempt %d failed: %s", attempt, exc)
            time.sleep(min(5 * attempt, 20))

    try:
        log.warning("Retrying with simpler BGM mix after ducking failures")
        return mix_bgm(video_path, bgm_path, output_path, use_ducking=False)
    except Exception as exc:
        last_exc = exc

    raise RuntimeError(f"BGM mix failed after retries: {last_exc}")


def finalize_without_bgm(video_path: Path, output_path: Path) -> Path:
    """
    Last-resort finalization. Keeps the completed subtitled voice video rather
    than failing the whole run because the optional BGM stage had a problem.
    """
    try:
        _ffmpeg(
            "-i", str(video_path),
            "-af", "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,loudnorm=I=-11:TP=-0.5:LRA=6",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            str(output_path),
            description="fallback loudness normalisation without BGM"
        )
    except Exception as exc:
        log.warning("Fallback loudness normalisation failed; copying subtitled video: %s", exc)
        shutil.copy2(video_path, output_path)
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
#  Master Assembly Function
# ──────────────────────────────────────────────────────────────────────────────

def assemble_video(
    voice_results: list[dict],
    visual_results: list[dict],
    ass_path: Path,
    run_dir: Path,
    bgm_path: Optional[Path] = None,
) -> Path:
    """
    Full video assembly pipeline.

    Returns path to distribution_ready.mp4
    """
    clips_dir = run_dir / "clips"
    raw_video_only = run_dir / "raw_video_only.mp4"
    master_voice_wav = run_dir / "master_voice.wav"
    raw_video = run_dir / "raw_video.mp4"
    subtitled_video = run_dir / "subtitled_video.mp4"
    branded_video = run_dir / "branded_video.mp4"
    final_video = run_dir / "distribution_ready.mp4"

    # Step 1: Build per-scene clips (video-only)
    log.info("Step 1/5: Building %d scene clips …", len(voice_results))
    clip_paths = build_scene_clips(voice_results, visual_results, clips_dir)

    # Step 2: Concatenate scene video clips (clean re-encode, frame-accurate)
    log.info("Step 2/5: Concatenating scene video clips with clean re-encode …")
    try:
        clean_concat_clips(clip_paths, raw_video_only)
    except Exception as exc:
        log.warning("Clean concat failed (%s); falling back to direct stream concat", exc)
        concatenate_clips(clip_paths, raw_video_only)

    # Step 2b: Create continuous master voice track and dynamic SFX track, then mux with video
    log.info("Step 2b/5: Creating unified master voice track & dynamic SFX track …")
    concatenate_voice_audio(voice_results, master_voice_wav)

    master_sfx_wav = run_dir / "master_sfx.wav"
    sfx_path = None
    try:
        total_voice_duration = get_audio_duration(master_voice_wav)
        sfx_path = build_sfx_track(voice_results, total_voice_duration, master_sfx_wav)
    except Exception as exc:
        log.warning("SFX generation failed (%s); continuing with pure voice track", exc)
        sfx_path = None

    if sfx_path and sfx_path.is_file():
        mixed_voice_sfx = run_dir / "voice_sfx_mix.wav"
        try:
            _ffmpeg(
                "-i", str(master_voice_wav),
                "-i", str(sfx_path),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo",
                "-c:a", "pcm_s16le",
                str(mixed_voice_sfx),
                description="mix master voice with sfx track"
            )
            mux_video_and_audio(raw_video_only, mixed_voice_sfx, raw_video)
        except Exception as e:
            log.warning("Voice/SFX mix failed (%s); falling back to pure voice track", e)
            mux_video_and_audio(raw_video_only, master_voice_wav, raw_video)
    else:
        mux_video_and_audio(raw_video_only, master_voice_wav, raw_video)

    # Step 3: Burn subtitles
    log.info("Step 3/5: Burning subtitles …")
    burn_subtitles(raw_video, ass_path, subtitled_video)

    # Step 4: Add brand mark
    log.info("Step 4/5: Adding brand protection mark …")
    overlay_brand_mark(subtitled_video, branded_video)

    # Step 5: Mix BGM
    import random
    if not bgm_path or not Path(bgm_path).is_file():
        bgm_dir = settings.ASSETS_DIR / "bgm"
        available_bgm = [
            track for track in bgm_dir.glob("*.mp3")
            if track.stat().st_size >= settings.BGM_MIN_BYTES
        ]
        bgm = random.choice(available_bgm) if available_bgm else settings.BGM_PATH
    else:
        bgm = bgm_path
    log.info(
        "Step 5/5: Mixing BGM (%s at %.1f dB) and normalising loudness …",
        bgm.name,
        settings.BGM_VOLUME_DB,
    )
    try:
        mix_bgm_with_retries(branded_video, bgm, final_video)
    except Exception as exc:
        log.error("BGM mix failed after retries; finalizing without BGM as emergency artifact: %s", exc)
        finalize_without_bgm(branded_video, final_video)

    # Duration safety guard: Ensure final duration does not exceed settings.MAX_VIDEO_DURATION
    try:
        from src.agents.quality_gate import _probe_video
        probe = _probe_video(final_video)
        final_dur = float(probe.get("format", {}).get("duration", 0.0))
        if final_dur > settings.MAX_VIDEO_DURATION:
            clamp_dur = settings.MAX_VIDEO_DURATION - 0.2
            log.warning(
                "Final video duration (%.2fs) exceeds max (%.1fs); clamping cleanly to %.2fs",
                final_dur,
                settings.MAX_VIDEO_DURATION,
                clamp_dur,
            )
            clamped_path = run_dir / "distribution_ready_clamped.mp4"
            fade_start = max(clamp_dur - 0.3, 0)
            _ffmpeg(
                "-i", str(final_video),
                "-t", f"{clamp_dur:.2f}",
                "-c:v", "copy",
                "-af", f"afade=t=out:st={fade_start:.2f}:d=0.3",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "48000",
                str(clamped_path),
                description="clamping final video duration to Shorts limit"
            )
            shutil.move(str(clamped_path), str(final_video))
    except Exception as clamp_err:
        log.warning("Duration clamp check non-fatal notice: %s", clamp_err)

    log.info(
        "🎬 Assembly complete → %s  (%d KB)",
        final_video.name,
        final_video.stat().st_size // 1024
    )
    return final_video
