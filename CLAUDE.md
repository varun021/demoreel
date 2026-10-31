# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
playwright install chromium          # plus `playwright install-deps chromium` if Linux libs are missing
demoreel create --config examples/demo-steps.json --output output/demo.webm [--headed]
```

Tests: `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 python -m pytest -q` (the override is only needed on Debian derivatives like LMDE that Playwright doesn't recognise). Single test: `python -m pytest -q -k <name>`. Browser tests use local servers in `tests/test_runner.py`. No linter configured.

## Architecture

Full developer guide: `docs/architecture.md`.

Local-first CLI (Typer + Playwright, Python 3.10+) that replays a JSON-scripted list of browser actions and records a video. Steps may carry `narration` (and `narrate: "before"|"after"`, default after; audio is cached by `tts.synthesize_cached`): `pipeline.create` then synthesizes WAVs (`tts.synthesize`, backends `kokoro`/`fake`), `run_demo` holds each step for its audio and fills a timeline, `subtitles.to_srt` writes the SRT and `export.export_mp4` muxes via ffmpeg (`.mp4` output only; plain `.webm` without narration bypasses all this). Kokoro needs Python ≤3.12 — use `.venv312` (the `.venv` is 3.13, no Kokoro). Modules in `src/demoreel/`:

- `cli.py` — single `create` command (the empty `@app.callback()` is required, otherwise Typer collapses the sole command and `demoreel create` breaks). Loads the JSON config and calls `run_demo`. `ValueError`/`KeyError`/`TypeError` become `typer.BadParameter` (config errors); anything else prints "Demo failed" and exits 1.
- `runner.py` — everything else:
  - `validate_config` checks the whole config before the browser launches. Its viewport bounds are 320x240 to 3840x2160. It also holds the per-action required-field rules. When adding an action, update `ALLOWED_ACTIONS`, this validation, and the dispatch chain in `_do_action`.
  - `_safe_url` resolves `navigate` URLs against `base_url` and rejects any other origin. A browser-level route guard in `run_demo` also aborts link clicks/redirects off-origin. Keep both checks on any new navigation path.
  - `run_demo` runs the steps in sequence. Waits are clamped (`wait.ms` ≤ 10000, `pause_ms` ≤ 5000, default pause 700ms after each step). Step timeout is `STEP_TIMEOUT_MS` (15s). Step failures are wrapped as `RuntimeError("Step N (action) failed: ...")`.

Output layout: Playwright records into `<stem>_raw/`. The video is then moved to the output path, and the raw dir is removed. Screenshots go to `<stem>_screenshots/`, named `NN-<name>.png` after sanitising the name. `run_demo` itself only writes `.webm`. Narrated runs write `.mp4` plus kept intermediates (`.webm`, `_audio/`, `.srt`, `_timeline.json`, `_ffmpeg.log`).

Config schema (see `examples/demo-steps.json`): `base_url`, optional `viewport`, `steps[]` with `action` in `navigate|click|fill|press|wait|screenshot`. Optional top-level `pace` (`fast|normal|slow`, scales built-in delays via `runner.PACES`; explicit `pause_ms`/`wait.ms` are not scaled). Each step takes an optional `pause_ms` and `highlight` (a selector; dims the page and rings that element for 600ms before the action). `click` and `fill` steps glide a fake cursor to the target and show a ripple (always on), and `fill` types key by key; `zoom` (1–3, click only) scales the page around the click point and back.
