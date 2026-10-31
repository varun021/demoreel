from pathlib import Path
import json
import typer
from .pipeline import create as build

app = typer.Typer(no_args_is_help=True, help="Record browser demos locally.")

@app.callback()
def main():
    """Record browser demos locally."""

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
