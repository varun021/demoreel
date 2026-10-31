# Demoreel

[![tests](https://github.com/varun021/demoreel/actions/workflows/ci.yml/badge.svg)](https://github.com/varun021/demoreel/actions/workflows/ci.yml)

Script a browser demo in JSON and get a narrated, captioned MP4. Everything runs locally: [Playwright](https://playwright.dev) records the browser, [Kokoro](https://github.com/hexgrad/kokoro) speaks the narration, and ffmpeg puts it together. No accounts, no uploads.

![Demoreel output: highlight ring, cursor, typing, zoom and captions](docs/assets/demo.gif)

*A GIF of the sample render (no sound). The MP4 also has the narration.*

```json
{
  "base_url": "http://localhost:3000",
  "steps": [
    {"action": "navigate", "url": "/", "narration": "This is Acme Notes, a notes app that organizes itself."},
    {"action": "click", "selector": "#cta", "highlight": "#cta", "zoom": 1.4, "narration": "Let's take a look at the features.", "narrate": "before"},
    {"action": "fill", "selector": "[data-testid='email']", "value": "demo@example.com", "narration": "Enter your email."}
  ]
}
```

- Highlight ring, gliding cursor, click ripple, zoom, and key-by-key typing, all baked into the video.
- Local text-to-speech with cached audio, burned-in subtitles and an `.srt`.
- A `pace` setting (`fast`, `normal`, `slow`) and a narration `--speed`.
- Stays on your site: navigation to other origins is blocked.

## Quick start

You need Python 3.12 or 3.13, ffmpeg (`sudo apt install ffmpeg`), and Chromium for Playwright.

```bash
git clone https://github.com/varun021/demoreel.git && cd demoreel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

demoreel init                                   # writes a starter demoreel.json for your own site

# or try the bundled sample: serve the sample site, then record it (offline test-tone voice, no Kokoro needed)
python3 -m http.server 3000 --directory examples/sample-site &
demoreel create -c examples/sample-demo.json -o output/demo.mp4 --tts fake
```

For the real Kokoro voice, see [Kokoro setup](#kokoro-setup).

## Configure

A config is a JSON file with `base_url`, an optional `viewport` and `pace`, and a list of `steps`.

- **Actions:** `navigate`, `click`, `fill`, `press`, `wait`, `screenshot`. Prefer stable selectors such as `[data-testid='email']`.
- **Any step:** `pause_ms`, `narration`, `narrate` (`"before"` or `"after"`), `highlight` (a selector).
- **Click steps:** `zoom` (above 1, up to 3).
- **Top level:** `pace` of `fast`, `normal` or `slow` (default `normal`) scales the built-in delays: cursor glide, zoom, highlight lead, ripple lead, typing speed and the default pause (x0.6 / x1 / x1.6). An explicit `pause_ms` or `wait.ms` is used as written.

Narration starts after the step's action by default. `"narrate": "before"` starts it as the step begins, while the highlight, cursor and click happen. Either way the step is held until the audio finishes.

Navigation is restricted to the origin (scheme, host, port) of `base_url`. This covers `navigate` steps and also link clicks and redirects, which are blocked at the browser level. Use a test account and demo data, and don't store passwords in the config. Be careful with actions that submit forms, delete data, send messages, or trigger real transactions. Only record sites you are authorized to test.

## Run

```bash
demoreel init --url http://localhost:5173        # starter config (--force to overwrite)
demoreel create -c examples/demo-steps.json -o output/demo.webm            # recording only
demoreel create -c examples/sample-demo.json -o output/demo.mp4            # narrated MP4 (Kokoro)
demoreel create -c examples/sample-demo.json -o output/demo.mp4 --tts fake # offline test tone
demoreel create -c examples/demo-steps.json -o output/demo.webm --headed   # watch the browser
```

Options for narrated runs: `--voice af_heart`, `--speed 1.2` (0.5 to 2.0; changing it re-renders the cached audio), `--no-burn-subtitles` (keep the `.srt` but don't hard-code it), `--av-offset-ms 200` (shift narration and subtitles later; negative is earlier). The CLI prints one progress line per step, and the MP4 fades in from and out to black.

## Output

`-o output/demo.webm` writes `output/demo.webm` and `output/demo_screenshots/NN-<name>.png` (NN is the step number). If a step fails, the run exits with code 1 and names the step. The partial recording stays in `output/demo_raw/`, no `demo.webm` is written, and a rerun clears `demo_raw/`.

`-o output/demo.mp4` with narration also keeps, next to the MP4: `demo.webm` (raw recording), `demo_audio/<hash>.wav` (cached narration, one per distinct line), `demo.srt`, `demo_timeline.json`, `demo_ffmpeg.log` (full command and ffmpeg output) and `demo_screenshots/`.

## Install notes (Linux)

- Needs the `venv` module (`sudo apt install python3-venv` on Debian/Ubuntu).
- Missing shared libraries when launching Chromium: `sudo "$(which playwright)" install-deps chromium`.
- Playwright doesn't recognise some Debian derivatives (for example LMDE) and fails with `does not support chromium on ubuntu20.04-x64`. Treat the host as Ubuntu 24.04 for both install and runs: `export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`.
- On a slow connection, if `pip` reports an incomplete download: `pip install --resume-retries 20 --timeout 60 -e ".[dev]"`.

## Kokoro setup

Kokoro needs Python 3.12 or older (its `numpy==1.26.4` pin has no 3.13 wheels), so use a separate environment with CPU-only PyTorch (about 196 MB, no CUDA). espeak-ng is bundled by the `espeakng-loader` package, so no apt install is needed.

```bash
pip install uv                                   # or any uv install
uv venv --python 3.12 .venv312 && source .venv312/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match "torch==2.14.1+cpu"
uv pip install -e ".[dev,kokoro]"
```

The `kokoro` extra also installs spaCy's `en_core_web_sm` (about 12 MB). The first narrated run downloads the Kokoro model (about 330 MB) from Hugging Face into `~/.cache/huggingface` and can take a few minutes; later runs start in about 25 s.

## Test

```bash
python -m pytest -q
```

Browser tests start a local HTTP server on 127.0.0.1 and never touch real websites. The real-Kokoro tests skip themselves when Kokoro isn't installed, so run the suite inside the Kokoro environment to include them. CI runs the rest on Python 3.12 and 3.13.

## Docs

[`docs/architecture.md`](docs/architecture.md) is the developer guide: data flow, modules, config schema, how to add an action or a TTS backend.

## Limitations

- Narration is English only (`lang_code="a"`), and the voice is one setting for the whole demo.
- Narration timing is wall-clock; if audio is slightly off, use `--av-offset-ms`.
- Actions must be specified explicitly; natural-language planning isn't built.
- Python 3.10 and 3.11 are allowed by `requires-python` but are not tested in CI.

## License

[MIT](LICENSE)
