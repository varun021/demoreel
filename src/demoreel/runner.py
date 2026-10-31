from pathlib import Path
from urllib.parse import urljoin, urlparse
import html
import re
import shutil
import time
from playwright.sync_api import sync_playwright

STEP_TIMEOUT_MS = 15000
CARD_SECONDS = 2.5  # default hold for the intro / outro title cards
ALLOWED_ACTIONS = {"navigate", "click", "fill", "press", "hover", "scroll", "wait", "screenshot"}

def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object.")
    base_url = config.get("base_url")
    if not isinstance(base_url, str):
        raise ValueError("Config requires a base_url.")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an http:// or https:// URL.")
    steps = config.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Config requires a non-empty steps array.")
    if config.get("pace", "normal") not in PACES:
        raise ValueError(f"pace must be one of {', '.join(PACES)}.")
    for key in ("intro", "outro"):
        card = config.get(key)
        if card is None:
            continue
        if not (isinstance(card, dict) and isinstance(card.get("title"), str) and card["title"].strip()):
            raise ValueError(f"{key} needs a non-empty title.")
        if not isinstance(card.get("subtitle", ""), str):
            raise ValueError(f"{key}.subtitle must be a string.")
        seconds = card.get("seconds", CARD_SECONDS)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0.5 <= seconds <= 10:
            raise ValueError(f"{key}.seconds must be a number from 0.5 to 10.")
    if not isinstance(config.get("storage_state", ""), str):
        raise ValueError("storage_state must be a file path string.")
    viewport = config.get("viewport", {})
    if not isinstance(viewport, dict):
        raise ValueError("viewport must be an object with width and height.")
    width, height = int(viewport.get("width", 1920)), int(viewport.get("height", 1080))
    if not (320 <= width <= 3840 and 240 <= height <= 2160):
        raise ValueError("Viewport must be between 320x240 and 3840x2160.")
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            raise ValueError(f"Step {i}: action must be one of {', '.join(sorted(ALLOWED_ACTIONS))}.")
        action = step["action"]
        if "narration" in step and not (isinstance(step["narration"], str) and step["narration"].strip()):
            raise ValueError(f"Step {i}: narration must be a non-empty string.")
        if step.get("narrate", "after") not in {"before", "after"}:
            raise ValueError(f"Step {i}: narrate must be \"before\" or \"after\".")
        if "highlight" in step and not isinstance(step["highlight"], str):
            raise ValueError(f"Step {i}: highlight must be a selector string.")
        if "zoom" in step and not (action == "click" and isinstance(step["zoom"], (int, float))
                                   and not isinstance(step["zoom"], bool) and 1 < step["zoom"] <= 3):
            raise ValueError(f"Step {i}: zoom must be a number above 1 and at most 3, on a click step.")
        if action in {"click", "fill", "press", "hover"} and not isinstance(step.get("selector"), str):
            raise ValueError(f"Step {i}: {action} requires selector.")
        if action == "fill" and "value" not in step:
            raise ValueError(f"Step {i}: fill requires value.")
        if action == "press" and not isinstance(step.get("key"), str):
            raise ValueError(f"Step {i}: press requires key.")
        if action == "scroll":
            by = step.get("by")
            has_by = isinstance(by, (int, float)) and not isinstance(by, bool)
            if isinstance(step.get("selector"), str) == has_by:  # both or neither
                raise ValueError(f"Step {i}: scroll needs exactly one of selector (scroll to it) or by (pixels, + is down).")
        if action == "navigate" and not isinstance(step.get("url"), str):
            raise ValueError(f"Step {i}: navigate requires url.")
    return base_url, steps, {"width": width, "height": height}

def _safe_url(base_url, target):
    resolved = urljoin(base_url.rstrip("/") + "/", target)
    base, dest = urlparse(base_url), urlparse(resolved)
    if (dest.scheme, dest.netloc) != (base.scheme, base.netloc):
        raise ValueError(f"Navigation blocked: {resolved} is outside the configured base origin.")
    return resolved

# Dims the page and rings the target; lives in the page so it is baked into the recording.
_HIGHLIGHT_JS = """el => {
  const r = el.getBoundingClientRect(), d = document.createElement('div');
  d.id = '__ds_highlight';
  d.style.cssText = `position:fixed;left:${r.left - 6}px;top:${r.top - 6}px;width:${r.width + 12}px;height:${r.height + 12}px;
    border:3px solid #f59e0b;border-radius:8px;box-shadow:0 0 0 9999px rgba(0,0,0,.35);
    pointer-events:none;z-index:2147483647`;
  document.body.appendChild(d);
}"""
PAGE_FADE_MS = 250
_FADE_IN_JS = f"""document.addEventListener('DOMContentLoaded', () =>
  document.documentElement.animate([{{opacity: 0}}, {{opacity: 1}}], {{duration: {PAGE_FADE_MS}, easing: 'ease-out'}}))"""
# Pace scales every built-in delay (cursor glide, zoom, highlight lead, ripple lead, typing, default pause).
# Explicit per-step `pause_ms` / `wait.ms` are taken as written.
PACES = {"fast": 0.6, "normal": 1.0, "slow": 1.6}
TYPE_DELAY_MS = 60  # per keystroke when typing into a field
HIGHLIGHT_LEAD_MS = 600  # how long the ring shows before the action fires

# Fake cursor (the recorder doesn't capture the OS pointer), glides to the click point. Lives on <html>
# so the body zoom below doesn't move it.
_CURSOR_JS = """async ({x, y, from, ms}) => {
  let c = document.getElementById('__ds_cursor');
  if (!c) {
    c = document.createElement('div');
    c.id = '__ds_cursor';
    const svg = "<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 22 22'><path d='M2 2l7 18 3-7 7-3z' fill='black' stroke='white' stroke-width='1.5'/></svg>";
    c.style.cssText = `position:fixed;left:0;top:0;width:40px;height:40px;z-index:2147483647;pointer-events:none;
      background:url("data:image/svg+xml,${encodeURIComponent(svg)}");transition:transform ${ms}ms ease-in-out;
      transform:translate(${from[0] - 4}px,${from[1] - 4}px)`;
    document.documentElement.appendChild(c);
    c.getBoundingClientRect();
  }
  c.style.transform = `translate(${x - 4}px,${y - 4}px)`;
  await new Promise(r => setTimeout(r, ms));
}"""

# Scales <body> around the click point, so that point stays under the cursor. scale=1 resets.
_ZOOM_JS = """async ({x, y, scale, ms}) => {
  const b = document.body, r = b.getBoundingClientRect();
  document.documentElement.style.overflow = 'hidden';
  b.style.transition = `transform ${ms}ms ease-in-out`;
  if (scale > 1) b.style.transformOrigin = `${x - r.left}px ${y - r.top}px`;
  b.style.transform = scale > 1 ? `scale(${scale})` : '';
  await new Promise(r => setTimeout(r, ms));
  if (scale === 1) document.documentElement.style.overflow = '';
}"""

_RIPPLE_JS = """({x, y}) => {
  const d = document.createElement('div');
  d.style.cssText = `position:fixed;left:${x - 30}px;top:${y - 30}px;width:60px;height:60px;border-radius:50%;
    border:3px solid #f59e0b;pointer-events:none;z-index:2147483647`;
  document.documentElement.appendChild(d);
  d.animate([{transform: 'scale(.2)', opacity: .9}, {transform: 'scale(1.4)', opacity: 0}], 600).onfinish = () => d.remove();
}"""

# Eased page scroll to an absolute offset (CSS smooth-scroll length varies by browser, this doesn't).
_SCROLL_JS = """async ({y, ms}) => {
  const max = document.documentElement.scrollHeight - innerHeight;
  const from = scrollY, to = Math.max(0, Math.min(y, max)), t0 = performance.now();
  await new Promise(done => {
    const tick = now => {
      const p = Math.min(1, (now - t0) / ms), e = p < .5 ? 2 * p * p : 1 - 2 * (1 - p) * (1 - p);
      scrollTo(0, from + (to - from) * e);
      p < 1 ? requestAnimationFrame(tick) : done();
    };
    requestAnimationFrame(tick);
  });
}"""
SCROLL_MS = 800

def _ms(fx, base):
    return round(base * fx.get("k", 1))

def _clear_highlight(page):
    try:
        page.evaluate("document.getElementById('__ds_highlight')?.remove()")
    except Exception:  # page navigated away; the overlay went with it
        pass

def _glide(page, step, fx):
    """Scroll the target into view and glide the cursor to its centre. Returns (locator, (x, y) or None)."""
    loc = page.locator(step["selector"])
    loc.scroll_into_view_if_needed()
    box = loc.bounding_box()
    if not box:
        return loc, None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.evaluate(_CURSOR_JS, {"x": x, "y": y, "from": fx.get("pos", [max(0, x - 150), y + 100]), "ms": _ms(fx, 450)})
    fx["pos"] = [x, y]
    _clear_highlight(page)  # a fixed overlay inside the zoomed body would drift
    return loc, (x, y)

def _fill(page, step, fx):
    """Glide to the field, click it, then type the value key by key."""
    loc, pt = _glide(page, step, fx)
    text = str(step["value"])
    if not pt:
        return loc.fill(text)
    page.evaluate(_RIPPLE_JS, {"x": pt[0], "y": pt[1]})
    loc.click()
    loc.fill("")
    loc.press_sequentially(text, delay=min(_ms(fx, TYPE_DELAY_MS), 3000 // max(1, len(text))))  # cap long values at ~3 s

def _hover(page, step, fx):
    """Glide the cursor onto the element and rest there (menus, tooltips)."""
    loc, pt = _glide(page, step, fx)
    loc.hover()

def _scroll(page, step, fx):
    if "selector" in step:  # centre the element in the viewport
        y = page.locator(step["selector"]).evaluate(
            "el => { const r = el.getBoundingClientRect(); return scrollY + r.top - (innerHeight - r.height) / 2; }")
    else:
        y = page.evaluate("scrollY") + step["by"]
    page.evaluate(_SCROLL_JS, {"y": y, "ms": _ms(fx, SCROLL_MS)})

def _click(page, step, fx):
    """Glide the cursor to the target, optionally zoom in, ripple, click, zoom back out."""
    loc, pt = _glide(page, step, fx)
    if not pt:
        return loc.click()
    x, y = pt
    zoom = step.get("zoom")
    if zoom:
        page.evaluate(_ZOOM_JS, {"x": x, "y": y, "scale": zoom, "ms": _ms(fx, 400)})
    page.evaluate(_RIPPLE_JS, {"x": x, "y": y})
    page.wait_for_timeout(_ms(fx, 150))
    loc.click()
    if zoom:
        try:
            page.evaluate(_ZOOM_JS, {"x": x, "y": y, "scale": 1, "ms": _ms(fx, 400)})
        except Exception:  # the click navigated; the new page starts unzoomed
            pass

def _run_step(page, base_url, screenshots_dir, index, step, fx):
    if "highlight" in step:
        page.locator(step["highlight"]).evaluate(_HIGHLIGHT_JS)
        page.wait_for_timeout(_ms(fx, HIGHLIGHT_LEAD_MS))
    try:
        _do_action(page, base_url, screenshots_dir, index, step, fx)
    finally:
        _clear_highlight(page)

def _do_action(page, base_url, screenshots_dir, index, step, fx):
    action = step["action"]
    if action == "navigate":
        page.goto(_safe_url(base_url, step["url"]), wait_until="domcontentloaded")
    elif action == "click":
        _click(page, step, fx)
    elif action == "fill":
        _fill(page, step, fx)
    elif action == "press":
        page.locator(step["selector"]).press(step["key"])
    elif action == "hover":
        _hover(page, step, fx)
    elif action == "scroll":
        _scroll(page, step, fx)
    elif action == "wait":
        page.wait_for_timeout(max(0, min(int(step.get("ms", 500)), 10000)))
    elif action == "screenshot":
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(step.get("name", f"step-{index}"))).strip("-")
        page.screenshot(path=str(screenshots_dir / f"{index:02d}-{name or 'screenshot'}.png"), full_page=True, animations="disabled")

def _show_card(page, card):
    """Title card: a styled page recorded like any other, so it shares the video's timeline."""
    title, sub = html.escape(card["title"]), html.escape(card.get("subtitle", ""))
    page.set_content(
        '<body style="margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;'
        'padding:0 8vw;box-sizing:border-box;background:linear-gradient(135deg,#1e1b4b,#4f46e5);color:#fff;'
        f'font-family:system-ui,sans-serif;text-align:center"><h1 style="font-size:72px;margin:0 0 16px">{title}</h1>'
        f'<p style="font-size:32px;margin:0;opacity:.8">{sub}</p></body>')
    page.wait_for_timeout(int(card.get("seconds", CARD_SECONDS) * 1000))

def save_login(url, out, headless=False, wait=input):
    """Open `url`, let the user sign in, then save cookies and local storage to `out` for `storage_state`."""
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("url must be an http:// or https:// URL.")
    out = Path(out).expanduser()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        context.new_page().goto(url)
        wait()
        context.storage_state(path=str(out))
        browser.close()
    out.chmod(0o600)  # it holds live session tokens

def run_demo(config, output_path, headed=False, narration_audio=None, timeline=None, log=None):
    """narration_audio: {step_index: (wav_path, seconds)}. If given with `timeline` (a list),
    each narrated step appends {"step","text","start","end","audio"} (seconds into the video).
    The step is held at least as long as its audio. `log(message)` gets one line per step."""
    log = log or (lambda message: None)
    narration_audio = narration_audio or {}
    base_url, steps, viewport = validate_config(config)
    output_path = Path(output_path).expanduser().resolve()
    if output_path.suffix.lower() != ".webm":
        raise ValueError("run_demo writes .webm; use pipeline.create for .mp4 output.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_path.parent / f"{output_path.stem}_screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_path.parent / f"{output_path.stem}_raw"
    shutil.rmtree(raw_dir, ignore_errors=True)  # stale recordings would be picked up otherwise
    state = config.get("storage_state")
    if state and not Path(state).expanduser().is_file():
        raise ValueError(f"storage_state file not found: {state}. Create one with: demoreel login <url> -o {state}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(viewport=viewport, record_video_dir=str(raw_dir), record_video_size=viewport,
                                      storage_state=str(Path(state).expanduser()) if state else None)
        base = urlparse(base_url)

        def _guard(route):
            # Blocks link clicks / redirects that leave the base origin, not just `navigate` steps.
            req = route.request
            u = urlparse(req.url)
            if req.is_navigation_request() and u.scheme in {"http", "https"} and (u.scheme, u.netloc) != (base.scheme, base.netloc):
                route.abort("blockedbyclient")
            else:
                route.continue_()

        context.route("**/*", _guard)
        context.add_init_script(_FADE_IN_JS)  # every page fades in, a soft cut between pages
        page = context.new_page()
        page.set_default_timeout(STEP_TIMEOUT_MS)
        fx = {"k": PACES[config.get("pace", "normal")]}  # "k": time scale; "pos": last cursor position (survives navigations)
        t0 = time.monotonic()  # ponytail: video starts at page creation; tune with the pipeline's av_offset_ms
        try:
            if "intro" in config:
                log("Intro card")
                _show_card(page, config["intro"])
            for index, step in enumerate(steps, 1):
                action = step["action"]
                step_start = time.monotonic() - t0
                log(f"[{index}/{len(steps)}] {action} {step.get('selector') or step.get('url') or step.get('name') or ''}".rstrip())
                try:
                    _run_step(page, base_url, screenshots_dir, index, step, fx)
                except Exception as exc:
                    raise RuntimeError(f"Step {index} ({action}) failed: {exc}") from exc
                pause_ms = max(0, min(int(step.get("pause_ms", _ms(fx, 700))), 5000))
                if index in narration_audio:
                    wav, seconds = narration_audio[index]
                    now = time.monotonic() - t0
                    start = step_start if step.get("narrate") == "before" else now  # default: after the action
                    if timeline is not None:
                        timeline.append({"step": index, "text": step["narration"].strip(), "start": start,
                                         "end": start + seconds, "audio": str(wav)})
                    pause_ms = max(pause_ms, int((start + seconds - now) * 1000) + _ms(fx, 300))  # hold until the audio ends
                page.wait_for_timeout(pause_ms)
            if "outro" in config:
                log("Outro card")
                _show_card(page, config["outro"])
        finally:
            context.close()
            browser.close()
    videos = list(raw_dir.glob("*.webm"))
    if not videos:
        raise RuntimeError("Playwright did not produce a recording.")
    videos[0].replace(output_path)
    shutil.rmtree(raw_dir, ignore_errors=True)
    return output_path
