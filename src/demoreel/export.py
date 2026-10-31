from pathlib import Path
import shutil
import subprocess


class ExportError(RuntimeError):
    pass


def _filter_path(p):
    # ffmpeg filter-graph escaping for a file path inside subtitles='...'
    return str(p).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


FADE_S = 0.4  # fade from/to black at the ends of the video


def build_command(video, timeline, srt, output, burn_subtitles=True, duration=None):
    """timeline entries need "start" and "audio" (wav path). With `duration` (seconds), the video fades
    in/out and the audio fades out. Returns the ffmpeg argv."""
    audios = [e for e in timeline if e.get("audio")]
    fade_out = f"t=out:st={max(0.0, duration - FADE_S):.2f}:d={FADE_S}" if duration else None
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(video)]
    for e in audios:
        cmd += ["-i", str(e["audio"])]
    parts, vmap, amap = [], "0:v", []
    vchain = []
    if burn_subtitles and timeline:
        vchain.append(f"subtitles='{_filter_path(srt)}':force_style='FontSize=11,Outline=1'")
    if duration:
        vchain += [f"fade=t=in:d={FADE_S}", f"fade={fade_out}"]
    if vchain:
        parts.append("[0:v]" + ",".join(vchain) + "[v]")
        vmap = "[v]"
    if audios:
        for i, e in enumerate(audios, 1):
            ms = max(0, round(e["start"] * 1000))
            parts.append(f"[{i}:a]adelay={ms}|{ms}[a{i}]")
        last = "[a1]"
        if len(audios) > 1:
            ins = "".join(f"[a{i}]" for i in range(1, len(audios) + 1))
            parts.append(f"{ins}amix=inputs={len(audios)}:normalize=0:duration=longest[amix]")
            last = "[amix]"
        if duration:  # no audio fade-in: the first line can start within 0.2 s
            parts.append(f"{last}afade={fade_out}[aout]")
            last = "[aout]"
        amap = ["-map", last]
    if parts:
        cmd += ["-filter_complex", ";".join(parts)]
    cmd += ["-map", vmap, *amap, "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audios:
        cmd += ["-c:a", "aac"]
    return cmd + ["-movflags", "+faststart", str(output)]


def _duration(video):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(video)], capture_output=True, text=True).stdout
        return float(out)
    except (OSError, ValueError):  # no ffprobe or no duration in the header: skip the fades
        return None


def export_mp4(video, timeline, srt, output, burn_subtitles=True, log_path=None):
    if not shutil.which("ffmpeg"):
        raise ExportError("ffmpeg not found. Install it: sudo apt install ffmpeg")
    cmd = build_command(video, timeline, srt, output, burn_subtitles, duration=_duration(video))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if log_path:
        Path(log_path).write_text(" ".join(cmd) + "\n\n" + res.stderr, encoding="utf-8")
    if res.returncode != 0:
        tail = "\n".join(res.stderr.strip().splitlines()[-8:])
        raise ExportError(f"ffmpeg failed (exit {res.returncode}). Log: {log_path}\n{tail}")
    return Path(output)
