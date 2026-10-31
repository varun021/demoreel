# Architecture (developer guide)

Local-first CLI that replays a JSON-scripted list of browser actions, records the browser, and optionally adds narration, subtitles and visual effects, producing a WebM or an MP4.

## Data flow

```
config.json ──► cli.create ──► pipeline.create
                                 │
                                 ├─ no narration + .webm output ─► runner.run_demo ─► WebM (done)
                                 │
                                 └─ narration + .mp4 output:
                                      1. tts.synthesize_cached   one WAV per narrated step (cached)
                                      2. runner.run_demo         records WebM, fills `timeline`
                                      3. pipeline                applies --av-offset-ms, writes timeline.json
                                      4. subtitles.to_srt        writes <name>.srt
                                      5. export.export_mp4       ffmpeg: burn subs, mix audio, H.264 + AAC
```

## Modules (`src/demoreel/`)

| Module | Responsibility |
|---|---|
| `cli.py` | Typer app. Commands: `create` and `init` (writes the `STARTER` config after running it through `validate_config`). Maps `ValueError`/`KeyError`/`TypeError` to a usage error, everything else to exit code 1. |
| `pipeline.py` | `create(...)`: chooses the WebM-only fast path or the narrated path, owns the output file layout. |
| `runner.py` | Config validation, the step loop, Playwright recording, the in-page effects (highlight, cursor, ripple, zoom), the cross-origin guard. |
| `tts.py` | `synthesize(text, wav_path, backend, voice) -> seconds`, `synthesize_cached(...)`, the `BACKENDS` dict (`fake`, `kokoro`). |
| `subtitles.py` | `to_srt(timeline)`. |
| `export.py` | `build_command(...)` (pure, testable) and `export_mp4(...)` (runs ffmpeg, writes a log). |

## Config schema

```json
{
  "base_url": "http://localhost:3000",
  "viewport": {"width": 1920, "height": 1080},
  "steps": [ { "action": "...", "pause_ms": 700 } ]
}
```

- `base_url` is required and must be http(s). `viewport` is optional (default 1920x1080, allowed range 320x240 to 3840x2160).
- Actions: `navigate` (`url`), `click` (`selector`), `fill` (`selector`, `value`), `press` (`selector`, `key`), `wait` (`ms`, clamped to 10000), `screenshot` (`name`).
- Optional top level: `pace` (`fast`, `normal`, `slow`; default `normal`). `runner.PACES` maps it to a time scale (0.6 / 1 / 1.6) kept in the `fx` state dict as `"k"`, and `_ms(fx, base)` applies it to the built-in delays: cursor glide (450 ms), zoom (400), highlight lead (600), ripple lead (150), typing delay (60/key), default pause (700) and the 300 ms hold after narration. Explicit `pause_ms` and `wait.ms` are never scaled, and neither is TTS speed or the page/video fades.
- Optional on any step: `pause_ms` (default 700, clamped to 5000), `narration` (non-empty string), `narrate` (`"before"` or `"after"`, default `"after"`), `highlight` (selector).
- Optional on `click` only: `zoom` (number above 1, at most 3).
- `demoreel.schema.json` mirrors these rules for editors (and flags unknown fields, which `validate_config` ignores). `tests/test_schema.py` checks the examples and the `init` starter against it, that its enums match `ALLOWED_ACTIONS` and `PACES`, and that both reject the same bad configs. Update both when you add a field.
- All validation lives in `runner.validate_config` and runs before the browser starts. A failed check raises `ValueError` with the step number.

## The step loop (`runner.run_demo`)

For each step, in order:

1. Record `step_start` (seconds since the page was created).
2. If `highlight` is set, draw the dim-and-ring overlay and wait `HIGHLIGHT_LEAD_MS` (600 ms).
3. Run the action. A `click` goes through `_click`: scroll into view, glide the fake cursor to the element centre (450 ms), clear the highlight, optionally zoom in (400 ms), show the ripple, wait 150 ms, click, zoom back out.
4. Clear any highlight (in a `finally`, so a failed step doesn't leave an overlay).
5. If the step has narration: the narration starts at `step_start` (`narrate: "before"`) or right now (`"after"`). One `timeline` entry is appended and the step is held until the audio ends, plus 300 ms: `pause = max(pause_ms, (start + seconds - now) * 1000 + 300)`.
6. Wait the pause.

A `fill` goes through `_fill`: glide to the field, ripple, click, clear it, then `press_sequentially` at `TYPE_DELAY_MS` (60 ms) per key, capped so a field takes about 3 s at most.

Any exception in a step is re-raised as `RuntimeError("Step N (action) failed: ...")`.

### Timeline contract

`run_demo` appends to the `timeline` list you pass in:

```json
{"step": 2, "text": "narration text", "start": 4.54, "end": 6.74, "audio": "/abs/path/<hash>.wav"}
```

Times are seconds into the video. They are measured from page creation, which is when Playwright starts recording. If sync looks slightly off on your machine, use `--av-offset-ms` (positive shifts later) before changing code. `subtitles.to_srt` and `export.build_command` consume exactly these keys.

## In-page effects

Playwright's video does not capture the OS pointer, so everything visible is DOM injected into the page. It is recorded as part of the page.

- **Highlight** (`_HIGHLIGHT_JS`): a fixed `<div>` whose huge `box-shadow` dims the page. Appended to `<body>`.
- **Cursor** (`_CURSOR_JS`): a fixed 40 px SVG arrow appended to `<html>`, moved with a CSS transition. The last position is kept in the Python `fx` dict (key `"pos"`), because a navigation destroys the element.
- **Ripple** (`_RIPPLE_JS`): a fixed circle animated with the Web Animations API, removes itself.
- **Zoom** (`_ZOOM_JS`): `transform: scale()` on `<body>` with the transform origin set to the click point, so the target stays under the cursor. `<html>` overflow is hidden while zoomed.

Known limitation: `position: fixed` elements inside `<body>` behave as if the body is their containing block while it is transformed, so sticky headers can shift during a zoom. The cursor, ripple and highlight avoid this by living on `<html>` or by being cleared before the zoom.

### Transitions and progress

- **Page fade:** `run_demo` registers `_FADE_IN_JS` as a context init script, so every page (including after a click that navigates) fades in over `PAGE_FADE_MS` (250 ms). Screenshots use `animations="disabled"` so they never catch a half-faded page.
- **Video fade:** `export.build_command(..., duration=...)` fades the video in and out (`FADE_S`, 0.4 s) and fades the audio out. There is deliberately no audio fade-in, because the first line can start within 0.2 s. `export_mp4` gets the duration from `ffprobe`; if it can't, the fades are skipped.
- **Progress:** `pipeline.create` and `run_demo` take an optional `log(message)` callback. The CLI passes `typer.echo`, which prints one line per stage and per step (`[3/8] click #cta`). Library callers get no output by default.

## Narration and caching

- `tts.synthesize_cached` stores `<sha1(backend, voice, text)[:12]>.wav` in `<name>_audio/` and returns `(path, seconds)`. A cache hit reads the duration from the WAV header. A truncated file is re-rendered. Old files are never deleted, so the folder can be wiped safely.
- Backends are functions `(text, wav_path, voice, speed) -> seconds`. To add one, write the function and register it in `BACKENDS`. Raise `TTSError` for anything the user can fix.
- Backends take `(text, wav_path, voice, speed)`. `--speed` (0.5 to 2.0) is passed to Kokoro and joins the cache key only when it is not 1.0, so audio cached at normal speed stays valid. The `fake` backend divides its duration by speed.
- `fake` is an offline test tone (about 0.35 s per word). Use it in tests and for quick previews.
- `kokoro` needs the optional extra and **Python 3.12 or older** (this repo keeps the Kokoro environment in `.venv312`). The pipeline object is loaded once per process (`lru_cache`). A missing model or spaCy package raises `TTSError` with the fix in the message.

## Safety: the origin guard

`run_demo` installs a route handler that aborts any navigation request leaving the origin of `base_url`, so a stray link or redirect can't send a recording to another site. `navigate` steps are checked separately by `_safe_url`. Only navigations are blocked, not sub-resource requests such as CDN scripts.

## Output layout

For `-o output/demo.mp4`:

```
output/demo.mp4              final video
output/demo.webm             raw recording (kept for debugging)
output/demo.srt              subtitles
output/demo_timeline.json    the timeline above, after --av-offset-ms
output/demo_ffmpeg.log       full ffmpeg command and stderr
output/demo_audio/*.wav      cached narration
output/demo_screenshots/     PNGs from `screenshot` steps
```

`output/` is git-ignored.

## Development

```bash
python3.12 -m venv .venv312 && . .venv312/bin/activate
pip install -e '.[dev,kokoro]'      # install CPU torch first, see README
python -m playwright install chromium
python -m pytest -q
```

- On Debian derivatives that Playwright doesn't recognise (such as LMDE), prefix with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`.
- Browser tests serve local pages from `tests/test_runner.py` fixtures, so they need no network. The real-Kokoro test is skipped when Kokoro isn't installed, and needs the model download (about 330 MB) on first run.
- `ffmpeg` must be on `PATH` for the export tests.
- There is no linter configured.

## Extending

**New action:** add it to `ALLOWED_ACTIONS`, add its required-field checks to `validate_config`, add a branch in `_do_action`, and add a row to the invalid-config test table.

**New per-step effect:** validate the field in `validate_config`, apply it in `_run_step` or `_click`, make sure it cleans up in the `finally`, and test it by screenshotting before, during and after (see `test_highlight_overlay_is_drawn_then_removed`).

**New export format:** write it next to `export_mp4` and have `pipeline.create` pick it from the output suffix. Keep `build_command`-style pure command builders so they can be tested without running ffmpeg.

## Gotchas

- Narrated runs must output `.mp4`; a narrated `.webm` raises a `ValueError`.
- `run_demo` alone only writes `.webm`.
- Each `click` step costs about 1.2 s of extra recording time (cursor travel, ripple lead), plus about 0.8 s with `zoom`.
- The recording is VP9 WebM and the export re-encodes to H.264 with `-pix_fmt yuv420p` so it plays everywhere.
