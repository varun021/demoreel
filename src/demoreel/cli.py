from pathlib import Path
import json
import typer
from .pipeline import create as build
from .runner import validate_config

app = typer.Typer(no_args_is_help=True, help="Record browser demos locally.")

STARTER = {
    "pace": "normal",
    "steps": [
        {"action": "navigate", "url": "/", "narration": "Welcome! This is the home page."},
        {"action": "click", "selector": "text=Get started", "highlight": "text=Get started", "zoom": 1.4,
         "narrate": "before", "narration": "Click here to get started."},
        {"action": "fill", "selector": "input[type=email]", "value": "demo@example.com",
         "narration": "Type your email."},
    ],
}


@app.command()
def init(
    path: Path = typer.Argument(Path("demoreel.json"), help="Where to write the starter config"),
    url: str = typer.Option("http://localhost:3000", "--url", help="The site to record (base_url)"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file"),
):
    """Write a starter config. Edit the selectors to match your site."""
    if path.exists() and not force:
        raise typer.BadParameter(f"{path} already exists (use --force to overwrite).")
    config = {"base_url": url, **STARTER}
    try:
        validate_config(config)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"Wrote {path}. Edit the selectors, then try: demoreel create -c {path} -o output/demo.mp4 --tts fake")


@app.command()
def create(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True),
    output: Path = typer.Option(Path("output/demo.webm"), "--output", "-o"),
    headed: bool = typer.Option(False, "--headed", help="Show the browser while recording"),
    tts: str = typer.Option("kokoro", "--tts", help="TTS backend: kokoro (local) or fake (test tone)"),
    voice: str = typer.Option("af_heart", "--voice", help="Kokoro voice name"),
    speed: float = typer.Option(1.0, "--speed", min=0.5, max=2.0, help="Narration speed (Kokoro; 1.0 = normal)"),
    burn_subtitles: bool = typer.Option(True, "--burn-subtitles/--no-burn-subtitles", help="Hard-code subtitles into the MP4"),
    av_offset_ms: int = typer.Option(0, "--av-offset-ms", help="Shift narration/subtitles vs video (+ = later)"),
):
    """Record a demo from a JSON config (WebM, or narrated MP4 with --output name.mp4)."""
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        result = build(data, output, headed, tts, voice, burn_subtitles, av_offset_ms, log=typer.echo, speed=speed)
        typer.echo(f"Recording saved: {result}")
    except (ValueError, KeyError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    except Exception as exc:
        typer.echo(f"Demo failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

if __name__ == "__main__":
    app()
