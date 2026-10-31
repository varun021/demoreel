import json
from pathlib import Path

import pytest

from demoreel.cli import STARTER
from demoreel.runner import ALLOWED_ACTIONS, PACES, validate_config

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "demoreel.schema.json").read_text())
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
BASE = "http://localhost:3000"


def test_schema_is_itself_valid():
    jsonschema.Draft7Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_match_schema_and_validator(path):
    cfg = json.loads(path.read_text())
    jsonschema.validate(cfg, SCHEMA)
    validate_config(cfg)


def test_init_starter_matches_schema():
    jsonschema.validate({"base_url": BASE, **STARTER}, SCHEMA)


def test_schema_enums_match_the_code():
    step = SCHEMA["definitions"]["step"]["properties"]
    assert set(step["action"]["enum"]) == ALLOWED_ACTIONS
    assert set(SCHEMA["properties"]["pace"]["enum"]) == set(PACES)


@pytest.mark.parametrize("cfg", [
    {"steps": [{"action": "wait"}]},
    {"base_url": "ftp://x", "steps": [{"action": "wait"}]},
    {"base_url": BASE, "steps": []},
    {"base_url": BASE, "pace": "warp", "steps": [{"action": "wait"}]},
    {"base_url": BASE, "viewport": {"width": 10, "height": 10}, "steps": [{"action": "wait"}]},
    {"base_url": BASE, "steps": [{"action": "explode"}]},
    {"base_url": BASE, "steps": [{"action": "click"}]},
    {"base_url": BASE, "steps": [{"action": "fill", "selector": "a"}]},
    {"base_url": BASE, "steps": [{"action": "press", "selector": "a"}]},
    {"base_url": BASE, "steps": [{"action": "navigate"}]},
    {"base_url": BASE, "steps": [{"action": "click", "selector": "a", "zoom": 1}]},
    {"base_url": BASE, "steps": [{"action": "click", "selector": "a", "zoom": 4}]},
    {"base_url": BASE, "steps": [{"action": "wait", "zoom": 2}]},
    {"base_url": BASE, "steps": [{"action": "wait", "narration": " "}]},
    {"base_url": BASE, "steps": [{"action": "wait", "narrate": "during"}]},
    {"base_url": BASE, "intro": {"subtitle": "no title"}, "steps": [{"action": "wait"}]},
    {"base_url": BASE, "outro": {"title": "x", "seconds": 0.1}, "steps": [{"action": "wait"}]},
    {"base_url": BASE, "intro": {"title": "x", "subtitle": 5}, "steps": [{"action": "wait"}]},
    {"base_url": BASE, "storage_state": 5, "steps": [{"action": "wait"}]},
    {"base_url": BASE, "steps": [{"action": "hover"}]},
    {"base_url": BASE, "steps": [{"action": "scroll"}]},
    {"base_url": BASE, "steps": [{"action": "scroll", "selector": "a", "by": 5}]},
    {"base_url": BASE, "steps": [{"action": "scroll", "by": "far"}]},
])
def test_schema_and_validator_reject_the_same_configs(cfg):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(cfg, SCHEMA)
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_schema_catches_typos_the_validator_ignores():
    cfg = {"base_url": BASE, "steps": [{"action": "click", "selector": "a", "zom": 2}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(cfg, SCHEMA)
