# Contributing

Thanks for helping. Demoreel is small on purpose, so most changes are a few lines.

## Set up

```bash
git clone https://github.com/varun021/demoreel.git && cd demoreel
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
python -m pytest -q
```

You also need `ffmpeg` on your `PATH`. To work on the Kokoro voice, follow the [Kokoro setup](README.md#kokoro-setup) (Python 3.12 or older). Everything else is tested with the offline `--tts fake` backend.

On Debian derivatives that Playwright doesn't recognise, prefix the tests with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64`.

## Before you open a pull request

- Run `python -m pytest -q`. CI runs it on Python 3.10 to 3.13.
- Add a test for new behavior. Browser tests serve local pages and never touch real sites. For an overlay or effect, screenshot before, during and after (see `test_highlight_overlay_is_drawn_then_removed`).
- Keep the change small and in the style of the surrounding code. No new dependency for something a few lines of the standard library can do.
- Update `README.md` and `docs/architecture.md` if you change the config or the CLI.

## Where things live

[`docs/architecture.md`](docs/architecture.md) explains the data flow and has short recipes for adding an action, a per-step effect, a TTS backend or an export format.

## Reporting bugs

Open an issue with the config you ran (remove secrets), the command, your OS and Python version, and the output. For a failed step, the `demo_ffmpeg.log` and the step message help most.

## Security and scope

Demoreel only records sites you point it at, and blocks navigation off the configured origin. Please keep that guard intact in any change that adds navigation.
