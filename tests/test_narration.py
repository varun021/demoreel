import json
import shutil
import subprocess
import wave

import pytest

import demoreel.tts as tts_mod

from demoreel.export import ExportError, build_command, export_mp4
from demoreel.pipeline import create
from demoreel.runner import validate_config
from demoreel.subtitles import to_srt
from demoreel.runner import run_demo
from demoreel.tts import TTSError, synthesize, synthesize_cached
from tests.test_runner import servers, site, _serve  # noqa: F401  (fixtures)


def test_srt_format():
    srt = to_srt([{"text": "Hello", "start": 0.5, "end": 3725.25}, {"text": "Bye", "start": 4, "end": 5}])
    assert srt.startswith("1\n00:00:00,500 --> 01:02:05,250\nHello\n\n2\n00:00:04,000 --> 00:00:05,000\nBye")


def test_fake_tts_writes_wav(tmp_path):
    d = synthesize("one two three four", tmp_path / "a" / "x.wav", backend="fake")
    with wave.open(str(tmp_path / "a" / "x.wav")) as w:
        assert abs(w.getnframes() / w.getframerate() - d) < 0.01
    assert d == pytest.approx(1.4)


def test_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="Unknown TTS"):
        synthesize("x", tmp_path / "x.wav", backend="nope")


def test_kokoro_not_installed(tmp_path, monkeypatch):
    import sys
    from demoreel import tts
    tts._kokoro_pipeline.cache_clear()
    monkeypatch.setitem(sys.modules, "kokoro", None)  # makes `import kokoro` raise ImportError
    with pytest.raises(TTSError, match="not installed"):
        synthesize("hi", tmp_path / "x.wav", backend="kokoro")


def test_kokoro_spacy_model_missing(tmp_path, monkeypatch):
    import sys, types
    from demoreel import tts
    tts._kokoro_pipeline.cache_clear()
    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KPipeline=None))
    monkeypatch.setattr("importlib.util.find_spec", lambda n: None)
    with pytest.raises(TTSError, match="spaCy model"):
        synthesize("hi", tmp_path / "x.wav", backend="kokoro")
    tts._kokoro_pipeline.cache_clear()


def test_kokoro_model_failure_is_wrapped(tmp_path, monkeypatch):
    import sys, types
    from demoreel import tts
    tts._kokoro_pipeline.cache_clear()

    def boom(**kw):
        raise OSError("no network")
    monkeypatch.setitem(sys.modules, "kokoro", types.SimpleNamespace(KPipeline=boom))
    monkeypatch.setattr("importlib.util.find_spec", lambda n: object())
    with pytest.raises(TTSError, match="330 MB"):
        synthesize("hi", tmp_path / "x.wav", backend="kokoro")
    tts._kokoro_pipeline.cache_clear()


def test_narration_validation():
    with pytest.raises(ValueError, match="narration"):
        validate_config({"base_url": "http://a", "steps": [{"action": "wait", "narration": " "}]})


def test_build_command_delays_and_mix():
    tl = [{"start": 1.5, "audio": "a.wav"}, {"start": 4, "audio": "b.wav"}]
    cmd = " ".join(build_command("v.webm", tl, "s.srt", "o.mp4"))
    assert "adelay=1500|1500" in cmd and "adelay=4000|4000" in cmd and "amix=inputs=2" in cmd
    assert "subtitles='s.srt'" in cmd
    assert "subtitles" not in " ".join(build_command("v.webm", tl, "s.srt", "o.mp4", burn_subtitles=False))


def test_build_command_fades_when_duration_known():
    tl = [{"start": 1, "audio": "a.wav"}]
    cmd = " ".join(build_command("v.webm", tl, "s.srt", "o.mp4", duration=10))
    assert "fade=t=in:d=0.4" in cmd and "fade=t=out:st=9.60" in cmd  # video
    assert "afade=t=out:st=9.60" in cmd and "afade=t=in" not in cmd  # audio: out only, first line must stay audible
    assert "fade" not in " ".join(build_command("v.webm", tl, "s.srt", "o.mp4"))  # unknown duration: no fades
    assert "[v]" in " ".join(build_command("v.webm", [], "s.srt", "o.mp4", duration=10))  # fades even without narration


def test_build_command_normalizes_loudness():
    cmd = " ".join(build_command("v.webm", [{"start": 1, "audio": "a.wav"}], "s.srt", "o.mp4"))
    assert "loudnorm=I=-16" in cmd and "aresample=48000" in cmd


def test_ffmpeg_failure_is_reported(tmp_path):
    (tmp_path / "bad.webm").write_text("not a video")
    with pytest.raises(ExportError, match="ffmpeg failed"):
        export_mp4(tmp_path / "bad.webm", [], tmp_path / "s.srt", tmp_path / "o.mp4", log_path=tmp_path / "log.txt")
    assert (tmp_path / "log.txt").exists()


def test_ffmpeg_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(ExportError, match="apt install ffmpeg"):
        export_mp4("v", [], "s", tmp_path / "o.mp4")


def test_narration_requires_mp4(tmp_path):
    cfg = {"base_url": "http://a", "steps": [{"action": "wait", "narration": "hi"}]}
    with pytest.raises(ValueError, match="mp4"):
        create(cfg, tmp_path / "o.webm", tts="fake")


def test_end_to_end_fake_tts_mp4(site, tmp_path):
    lines = []
    cfg = {"base_url": site, "viewport": {"width": 640, "height": 480}, "steps": [
        {"action": "navigate", "url": "/", "pause_ms": 200, "narration": "Welcome to the home page."},
        {"action": "click", "selector": "#b", "pause_ms": 200, "narration": "Now we click the button."},
        {"action": "wait", "ms": 100},
    ]}
    out = create(cfg, tmp_path / "demo.mp4", tts="fake", log=lines.append)
    assert [l for l in lines if l.startswith("[")] == ["[1/3] navigate /", "[2/3] click #b", "[3/3] wait"]
    assert lines[0].startswith("Narrating 2") and lines[-1] == "Exporting MP4"
    for name in ["demo.webm", "demo.srt", "demo_timeline.json", "demo_ffmpeg.log"] :
        assert (tmp_path / name).exists(), name
    assert len(list((tmp_path / "demo_audio").glob("*.wav"))) == 2
    tl = json.loads((tmp_path / "demo_timeline.json").read_text())
    assert tl[0]["end"] <= tl[1]["start"] + 0.01  # steps hold for the full narration
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.split()
    assert "h264" in probe and "aac" in probe
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert dur >= tl[1]["end"]


def test_webm_workflow_unchanged(site, tmp_path):
    cfg = {"base_url": site, "steps": [{"action": "navigate", "url": "/", "pause_ms": 100}]}
    out = create(cfg, tmp_path / "plain.webm")
    assert out.suffix == ".webm" and not (tmp_path / "plain.srt").exists()


def _kokoro_ready():
    try:
        import importlib.util
        return all(importlib.util.find_spec(m) for m in ("kokoro", "en_core_web_sm"))
    except Exception:
        return False


@pytest.mark.skipif(not _kokoro_ready(), reason="Kokoro not installed (use .venv312 with .[kokoro])")
def test_real_kokoro(tmp_path):
    d = synthesize("Hello from the demo studio.", tmp_path / "k.wav", backend="kokoro")
    with wave.open(str(tmp_path / "k.wav")) as w:
        assert 1 < d < 6 and abs(w.getnframes() / w.getframerate() - d) < 0.01


def test_synthesize_cached_reuses_same_text_and_voice(tmp_path, monkeypatch):
    calls = []
    real = tts_mod.BACKENDS["fake"]
    monkeypatch.setitem(tts_mod.BACKENDS, "fake", lambda *a: calls.append(a) or real(*a))
    first = synthesize_cached("hello there", tmp_path, "fake", "v1")
    again = synthesize_cached("hello there", tmp_path, "fake", "v1")
    synthesize_cached("hello there", tmp_path, "fake", "v2")
    assert first == again and len(calls) == 2  # v2 is a different key


def test_narrate_before_starts_with_the_step(site, tmp_path):
    wav, secs = synthesize_cached("Click it.", tmp_path, "fake")
    starts = {}
    for mode in ("before", "after"):
        step = {"action": "click", "selector": "#b", "highlight": "#b", "narration": "Click it.", "narrate": mode}
        timeline = []
        run_demo({"base_url": site, "steps": [{"action": "navigate", "url": "/"}, step]},
                 tmp_path / f"{mode}.webm", narration_audio={2: (wav, secs)}, timeline=timeline)
        starts[mode] = timeline[0]["start"]
    assert starts["after"] - starts["before"] > 1.0  # highlight + cursor travel happen in between


def test_narrate_must_be_before_or_after():
    with pytest.raises(ValueError, match="narrate"):
        validate_config({"base_url": "http://a", "steps": [{"action": "wait", "narrate": "during"}]})


def test_speed_shortens_audio_is_cached_separately_and_is_validated(tmp_path):
    normal = synthesize("one two three four five six", tmp_path / "n.wav", backend="fake")
    fast = synthesize("one two three four five six", tmp_path / "f.wav", backend="fake", speed=2.0)
    assert fast == pytest.approx(normal / 2, rel=0.01)
    a, _ = synthesize_cached("hello there friend", tmp_path, "fake")
    b, _ = synthesize_cached("hello there friend", tmp_path, "fake", speed=1.5)
    assert a != b
    for bad in (0.4, 2.5):
        with pytest.raises(ValueError, match="speed"):
            synthesize("hi", tmp_path / "x.wav", backend="fake", speed=bad)


@pytest.mark.skipif(not _kokoro_ready(), reason="Kokoro not installed (use .venv312 with .[kokoro])")
def test_real_kokoro_speed(tmp_path):
    text = "Speed changes how long this sentence takes to say."
    assert synthesize(text, tmp_path / "f.wav", speed=1.5) < synthesize(text, tmp_path / "n.wav") * 0.85
