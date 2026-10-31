def _ts(seconds):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(timeline):
    """timeline: [{"text", "start", "end"}] in seconds -> SRT text."""
    blocks = [f"{i}\n{_ts(e['start'])} --> {_ts(e['end'])}\n{e['text']}\n"
              for i, e in enumerate(timeline, 1)]
    return "\n".join(blocks)
