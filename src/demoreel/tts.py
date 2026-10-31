"""Text-to-speech behind one function: synthesize(text, wav_path, backend, voice, speed) -> seconds."""
from functools import lru_cache
from pathlib import Path
import hashlib
import math
import wave
from array import array

SAMPLE_RATE = 24000  # Kokoro's native rate; the fake backend matches it


class TTSError(RuntimeError):
    pass


def _write_wav(path, samples):
    """samples: iterable of floats in [-1, 1]."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(array("h", (int(max(-1, min(1, s)) * 32767) for s in samples)).tobytes())  # native-endian == little on x86/ARM


def _fake(text, wav_path, voice, speed):
    """Offline test backend: a quiet 440 Hz tone, ~0.35 s per word."""
    seconds = max(0.8, 0.35 * len(text.split())) / speed
    n = int(seconds * SAMPLE_RATE)
    _write_wav(wav_path, (0.1 * math.sin(2 * math.pi * 440 * i / SAMPLE_RATE) for i in range(n)))
    return n / SAMPLE_RATE


@lru_cache(maxsize=1)
def _kokoro_pipeline():
    try:
        from kokoro import KPipeline
    except ImportError as exc:
        raise TTSError(
            "Kokoro is not installed. Use Python 3.10-3.12 and run: pip install -e '.[kokoro]'") from exc
    import importlib.util
    if importlib.util.find_spec("en_core_web_sm") is None:  # else spaCy tries pip and sys.exit()s
        raise TTSError("spaCy model missing. Run: pip install -e '.[kokoro]'")
    try:
        return KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    except (Exception, SystemExit) as exc:  # network / Hugging Face / weights problems
        raise TTSError(
            f"Could not load the Kokoro model ({exc}). The first run downloads ~330 MB from "
            "Hugging Face; check your connection, then retry.") from exc


def _kokoro(text, wav_path, voice, speed):
    pipeline = _kokoro_pipeline()
    import numpy as np
    try:
        chunks = [np.asarray(audio, dtype="float32") for _, _, audio in pipeline(text, voice=voice, speed=speed)]
    except Exception as exc:
        raise TTSError(f"Kokoro failed on {text[:40]!r}: {exc}") from exc
    if not chunks:
        raise TTSError(f"Kokoro produced no audio for {text[:40]!r}.")
    audio = np.concatenate(chunks)
    _write_wav(wav_path, audio.tolist())
    return len(audio) / SAMPLE_RATE


BACKENDS = {"fake": _fake, "kokoro": _kokoro}


def synthesize(text, wav_path, backend="kokoro", voice="af_heart", speed=1.0):
    if backend not in BACKENDS:
        raise ValueError(f"Unknown TTS backend {backend!r}. Choose from: {', '.join(BACKENDS)}.")
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Speech speed must be between 0.5 and 2.0.")
    wav_path = Path(wav_path)
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    return BACKENDS[backend](text, wav_path, voice, speed)


def synthesize_cached(text, folder, backend="kokoro", voice="af_heart", speed=1.0):
    """Reuse an earlier render of the same backend+voice+text. Returns (wav_path, seconds)."""
    # speed joins the key only when changed, so audio cached at normal speed stays valid
    key = hashlib.sha1("\0".join([backend, voice, text, *([str(speed)] if speed != 1 else [])]).encode()).hexdigest()[:12]
    wav = Path(folder) / f"{key}.wav"
    if wav.exists():
        try:
            with wave.open(str(wav)) as w:
                return wav, w.getnframes() / w.getframerate()
        except (wave.Error, EOFError):  # truncated by an interrupted run: render again
            pass
    return wav, synthesize(text, wav, backend, voice, speed)
