"""WebM-only runs pass straight to the recorder; narrated runs add TTS, SRT and an MP4 export."""
from pathlib import Path
import json

from .export import export_mp4
from .runner import run_demo, validate_config
from .subtitles import to_srt
from .tts import synthesize_cached


def create(config, output, headed=False, tts="kokoro", voice="af_heart", burn_subtitles=True, av_offset_ms=0, log=None, speed=1.0):
    log = log or (lambda message: None)
    output = Path(output).expanduser().resolve()
    _, steps, _ = validate_config(config)
    narrated = {i: s["narration"].strip() for i, s in enumerate(steps, 1) if "narration" in s}
    if output.suffix.lower() == ".webm" and not narrated:
        return run_demo(config, output, headed=headed, log=log)
    if output.suffix.lower() != ".mp4":
        raise ValueError("Narrated demos need .mp4 output (use --output name.mp4).")

    stem, folder = output.stem, output.parent
    webm = folder / f"{stem}.webm"  # intermediates stay on disk for debugging
    audio_dir = folder / f"{stem}_audio"
    log(f"Narrating {len(narrated)} line(s) ({tts}, cached lines are reused)")
    audio = {i: synthesize_cached(text, audio_dir, tts, voice, speed) for i, text in narrated.items()}

    timeline = []
    run_demo(config, webm, headed=headed, narration_audio=audio, timeline=timeline, log=log)
    for e in timeline:
        e["start"] = max(0.0, e["start"] + av_offset_ms / 1000)
        e["end"] += av_offset_ms / 1000
    (folder / f"{stem}_timeline.json").write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    srt = folder / f"{stem}.srt"
    srt.write_text(to_srt(timeline), encoding="utf-8")
    log("Exporting MP4")
    return export_mp4(webm, timeline, srt, output, burn_subtitles, log_path=folder / f"{stem}_ffmpeg.log")
