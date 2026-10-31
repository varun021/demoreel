import functools
import json
import re
import http.server
import threading

import pytest
from typer.testing import CliRunner

from demoreel.cli import app
from playwright.sync_api import sync_playwright

from demoreel.runner import _FADE_IN_JS, _click, _do_action, _fill, save_login, _safe_url, validate_config, run_demo

BASE = "http://localhost:3000"
OK = {"base_url": BASE, "steps": [{"action": "wait"}]}


@pytest.mark.parametrize("cfg, msg", [
    ([], "JSON object"),
    ({}, "base_url"),
    ({"base_url": "ftp://x", "steps": [{"action": "wait"}]}, "http"),
    ({"base_url": BASE, "steps": []}, "steps"),
    ({**OK, "viewport": {"width": 10, "height": 10}}, "Viewport"),
    ({**OK, "viewport": "big"}, "viewport"),
    ({"base_url": BASE, "steps": [{"action": "explode"}]}, "action"),
    ({"base_url": BASE, "steps": [{"action": "click"}]}, "selector"),
    ({"base_url": BASE, "steps": [{"action": "fill", "selector": "a"}]}, "value"),
    ({"base_url": BASE, "steps": [{"action": "press", "selector": "a"}]}, "key"),
    ({"base_url": BASE, "steps": [{"action": "navigate"}]}, "url"),
    ({"base_url": BASE, "steps": [{"action": "wait", "highlight": 5}]}, "highlight"),
    ({"base_url": BASE, "steps": [{"action": "click", "selector": "a", "zoom": 1}]}, "zoom"),
    ({"base_url": BASE, "steps": [{"action": "click", "selector": "a", "zoom": 4}]}, "zoom"),
    ({"base_url": BASE, "steps": [{"action": "wait", "zoom": 2}]}, "zoom"),
    ({**OK, "pace": "warp"}, "pace"),
    ({**OK, "intro": {"subtitle": "no title"}}, "intro needs a non-empty title"),
    ({**OK, "outro": {"title": "x", "seconds": 0.1}}, "outro.seconds"),
    ({**OK, "intro": {"title": "x", "subtitle": 5}}, "intro.subtitle"),
    ({**OK, "storage_state": 5}, "storage_state"),
    ({"base_url": BASE, "steps": [{"action": "hover"}]}, "hover requires selector"),
    ({"base_url": BASE, "steps": [{"action": "scroll"}]}, "scroll needs exactly one"),
    ({"base_url": BASE, "steps": [{"action": "scroll", "selector": "a", "by": 5}]}, "scroll needs exactly one"),
    ({"base_url": BASE, "steps": [{"action": "scroll", "by": "far"}]}, "scroll needs exactly one"),
])
def test_invalid_config(cfg, msg):
    with pytest.raises(ValueError, match=msg):
        validate_config(cfg)


def test_valid_config():
    assert validate_config(OK)[2] == {"width": 1920, "height": 1080}


@pytest.mark.parametrize("target", [
    "https://evil.example/", "//evil.example/x", "http://localhost:3001/",
    "https://localhost:3000/", "javascript:alert(1)",
])
def test_cross_origin_blocked(target):
    with pytest.raises(ValueError, match="blocked"):
        _safe_url(BASE, target)


def test_same_origin_allowed():
    assert _safe_url(BASE, "/a") == BASE + "/a"


def test_bad_output_extension(tmp_path):
    with pytest.raises(ValueError, match="webm"):
        run_demo(OK, tmp_path / "x.mp4")


# --- end-to-end against a local server (needs Playwright Chromium) ---

def _serve(root, hits=None):
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(root), **k)

        def log_message(self, *a):
            if hits is not None:
                hits.append(a)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def servers(tmp_path):
    other_root, root = tmp_path / "other", tmp_path / "site"
    other_root.mkdir(), root.mkdir()
    (other_root / "index.html").write_text("other origin")
    other_hits = []
    other = _serve(other_root, other_hits)
    (root / "index.html").write_text(
        '<h1>Home</h1><button id="b" onclick="document.title=\'clicked\'">Go</button>'
        f'<a id="out" href="http://127.0.0.1:{other.server_port}/">out</a>')
    site = _serve(root)
    yield f"http://127.0.0.1:{site.server_port}", other_hits
    site.shutdown(), other.shutdown()


@pytest.fixture
def site(servers):
    return servers[0]


def test_records_webm_and_screenshot(site, tmp_path):
    cfg = {"base_url": site, "viewport": {"width": 640, "height": 480}, "steps": [
        {"action": "navigate", "url": "/", "pause_ms": 300},
        {"action": "screenshot", "name": "home page!", "pause_ms": 100},
        {"action": "click", "selector": "#b", "pause_ms": 300},
    ]}
    out = run_demo(cfg, tmp_path / "out" / "demo.webm")
    assert out.read_bytes()[:4] == b"\x1a\x45\xdf\xa3"  # EBML header = WebM
    assert (tmp_path / "out" / "demo_screenshots" / "02-home-page.png").stat().st_size > 0
    assert not (tmp_path / "out" / "demo_raw").exists()


def test_highlight_overlay_is_drawn_then_removed(site, tmp_path):
    cfg = {"base_url": site, "viewport": {"width": 640, "height": 480}, "steps": [
        {"action": "navigate", "url": "/", "pause_ms": 100},
        {"action": "screenshot", "name": "plain", "pause_ms": 100},
        {"action": "screenshot", "name": "ring", "highlight": "#b", "pause_ms": 100},
        {"action": "screenshot", "name": "after", "pause_ms": 100},
    ]}
    run_demo(cfg, tmp_path / "demo.webm")
    shots = tmp_path / "demo_screenshots"
    read = lambda n: next(shots.glob(f"*-{n}.png")).read_bytes()
    assert read("ring") != read("plain")  # overlay captured
    assert read("after") == read("plain")  # and cleaned up


@pytest.mark.parametrize("zoom", [None, 2])
def test_click_shows_cursor_clicks_and_resets_zoom(site, zoom):
    step = {"selector": "#b", **({"zoom": zoom} if zoom else {})}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(site)
        _click(page, step, {})
        assert page.title() == "clicked"  # the real click still landed
        assert page.evaluate("!!document.getElementById('__ds_cursor')")
        assert page.evaluate("document.body.style.transform") == ""  # zoomed back out
        browser.close()


def test_link_click_to_other_origin_is_blocked(servers, tmp_path):
    site, other_hits = servers
    cfg = {"base_url": site, "steps": [
        {"action": "navigate", "url": "/", "pause_ms": 100},
        {"action": "click", "selector": "#out", "pause_ms": 500},
    ]}
    run_demo(cfg, tmp_path / "demo.webm")
    assert other_hits == []  # the other origin never received a request


def test_failed_step_reports_index(site, tmp_path, monkeypatch):
    cfg = {"base_url": site, "steps": [{"action": "navigate", "url": "/"},
                                      {"action": "click", "selector": "#missing"}]}
    monkeypatch.setattr("demoreel.runner.STEP_TIMEOUT_MS", 500)
    with pytest.raises(RuntimeError, match="Step 2 .click. failed"):
        run_demo(cfg, tmp_path / "demo.webm")


def test_cli_errors(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"base_url": "nope", "steps": []}')
    res = CliRunner().invoke(app, ["create", "-c", str(bad), "-o", str(tmp_path / "o.webm")])
    assert res.exit_code != 0


def test_cli_create_subcommand_exists():
    res = CliRunner().invoke(app, ["create", "--help"])
    plain = re.sub(r"\x1b\[[0-9;]*m", "", res.output)  # CI forces colour, which can split "--config" into styled pieces
    assert res.exit_code == 0 and "--config" in plain


def test_fill_types_into_the_field_with_the_cursor(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(
        '<input id="e" value="old"><script>window.keys = [];'
        'e.addEventListener("keydown", ev => keys.push(ev.key))</script>')
    srv = _serve(root)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{srv.server_port}")
            _fill(page, {"selector": "#e", "value": "hi!"}, {})
            assert page.input_value("#e") == "hi!"  # old value replaced
            assert page.evaluate("keys.join('')").endswith("hi!")  # typed key by key, not set at once
            assert page.evaluate("!!document.getElementById('__ds_cursor')")
            browser.close()
    finally:
        srv.shutdown()


def test_pages_fade_in(site):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_init_script(_FADE_IN_JS)
        page = context.new_page()
        page.goto(site)
        assert page.evaluate("document.documentElement.getAnimations().length") >= 1
        browser.close()


def test_pace_scales_builtin_delays_but_not_explicit_pauses(site, tmp_path):
    def narrated_start(pace):
        timeline = []
        steps = [{"action": "navigate", "url": "/"}, {"action": "click", "selector": "#b", "highlight": "#b"},
                 {"action": "click", "selector": "#b", "narration": "x"}]
        wav = tmp_path / "x.wav"
        run_demo({"base_url": site, "pace": pace, "steps": steps}, tmp_path / f"{pace}.webm",
                 narration_audio={3: (wav, 0.1)}, timeline=timeline)
        return timeline[0]["start"]
    # steps 1-3 contain only built-in delays (pauses, highlight lead, cursor glide, ripple lead)
    assert narrated_start("slow") - narrated_start("fast") > 2  # about 3.2 s expected


def test_init_writes_a_valid_config_and_protects_existing_files(tmp_path):
    target = tmp_path / "demoreel.json"
    res = CliRunner().invoke(app, ["init", str(target), "--url", "http://localhost:5173"])
    assert res.exit_code == 0
    cfg = json.loads(target.read_text())
    assert cfg["base_url"] == "http://localhost:5173"
    validate_config(cfg)  # the starter must pass the real validator
    again = CliRunner().invoke(app, ["init", str(target)])
    assert again.exit_code != 0 and json.loads(target.read_text())["base_url"] == "http://localhost:5173"
    assert CliRunner().invoke(app, ["init", str(target), "--force"]).exit_code == 0
    assert json.loads(target.read_text())["base_url"] == "http://localhost:3000"
    assert CliRunner().invoke(app, ["init", str(tmp_path / "b.json"), "--url", "nope"]).exit_code != 0


def test_hover_and_scroll_actions(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(
        '<button id="h" onmouseover="document.title=\'hovered\'">menu</button>'
        '<div style="height:2000px"></div><h2 id="end">bottom</h2><div style="height:600px"></div>')
    srv = _serve(root)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.goto(f"http://127.0.0.1:{srv.server_port}")
            run = lambda step: _do_action(page, "http://x", tmp_path, 1, step, {"k": 0.2})
            run({"action": "hover", "selector": "#h"})
            assert page.title() == "hovered"
            assert page.evaluate("!!document.getElementById('__ds_cursor')")  # the cursor travelled there
            run({"action": "scroll", "by": 500})
            assert page.evaluate("scrollY") == 500
            run({"action": "scroll", "by": -200})
            assert page.evaluate("scrollY") == 300
            run({"action": "scroll", "selector": "#end"})
            top = page.evaluate("document.getElementById('end').getBoundingClientRect().top")
            assert 200 < top < 400  # roughly centred in the 600 px viewport
            run({"action": "scroll", "by": -99999})
            assert page.evaluate("scrollY") == 0  # clamped at the top
            browser.close()
    finally:
        srv.shutdown()


def test_intro_and_outro_cards_extend_the_timeline(site, tmp_path):
    lines, timeline = [], []
    wav = tmp_path / "x.wav"
    cfg = {"base_url": site, "intro": {"title": "Hello <b>", "seconds": 1}, "outro": {"title": "Bye", "seconds": 1},
           "steps": [{"action": "navigate", "url": "/", "narration": "hi", "pause_ms": 100}]}
    out = run_demo(cfg, tmp_path / "demo.webm", narration_audio={1: (wav, 0.1)}, timeline=timeline, log=lines.append)
    assert out.exists() and "Intro card" in lines and "Outro card" in lines
    assert timeline[0]["start"] >= 1.0  # narration starts after the intro card, so subtitles and audio stay in sync


def test_login_session_is_saved_and_reused(tmp_path):
    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "sid=abc123; Path=/")
            self.end_headers()
            self.wfile.write(b"<h1>app</h1>")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    try:
        state = tmp_path / "auth.json"
        save_login(url, state, headless=True, wait=lambda: None)
        assert "abc123" in state.read_text() and state.stat().st_mode & 0o077 == 0  # saved, owner-only
        seen.clear()
        cfg = {"base_url": url, "storage_state": str(state), "steps": [{"action": "navigate", "url": "/", "pause_ms": 100}]}
        run_demo(cfg, tmp_path / "demo.webm")
        assert seen and "sid=abc123" in seen[0]  # the demo's very first request already carries the session
    finally:
        srv.shutdown()
    with pytest.raises(ValueError, match="demoreel login"):
        run_demo({**cfg, "storage_state": str(tmp_path / "missing.json")}, tmp_path / "x.webm")
    with pytest.raises(ValueError, match="http"):
        save_login("file:///etc/passwd", tmp_path / "a.json", headless=True, wait=lambda: None)
