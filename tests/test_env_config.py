"""Tests for how the documented environment variables are read.

`docs/CONFIGURATION.md` publishes a table of environment variables with their
defaults, and three of them are read at import time. The case worth testing is a
variable that is *present but empty*: that is what an empty CI variable, an empty
line in a sourced .env and PowerShell's `$env:X = ""` all produce, and it is not
the same thing as an absent one.

Import-time behaviour is checked in a subprocess on purpose. Reloading a module
in-process would replace the object `tests/conftest.py` and the rest of the suite
already hold, and the question being asked -- what does a fresh interpreter
compute? -- is exactly the one a reload cannot answer.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys

import pytest

import moon_extract

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The four variables this file is about, all of which the docs table names.
CONFIG_VARS = ("MOON_CDP_PORT", "MOON_DN_LANES", "MOON_DN_CAPTCHA_WAIT", "MOON_DEBUG")

PROBE = (
    "import moon_extract as m, moon_bridge as b\n"
    "print(repr([m.CDP_PORT, m.DN_LANES, m.DN_MANUAL_CAPTCHA_TIMEOUT,"
    " m.DEBUG, b.DEBUG]))\n"
)


def import_probe(**env) -> dict:
    """Import both modules in a fresh interpreter with only `env` set.

    Everything else is scrubbed, so a variable sitting in the developer's own
    shell cannot change the answer.
    """
    child = dict(os.environ)
    for name in CONFIG_VARS:
        child.pop(name, None)
    child.update(env)
    proc = subprocess.run([sys.executable, "-c", PROBE], cwd=str(ROOT), env=child,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"import failed:\n{proc.stderr[-800:]}"
    port, lanes, wait, extract_debug, bridge_debug = ast.literal_eval(proc.stdout.strip())
    return {"port": port, "lanes": lanes, "wait": wait,
            "extract_debug": extract_debug, "bridge_debug": bridge_debug}


# ── the regression: a configured value must not be able to kill the process ──
# Before this, `MOON_CDP_PORT=` raised ValueError out of module level, so the
# traceback landed before argument parsing and `--help` never ran.

@pytest.mark.parametrize("junk", ["", " ", "abc", "9222,9223", "not-a-number"])
@pytest.mark.parametrize("var", CONFIG_VARS)
def test_import_survives_an_empty_or_junk_value(var, junk):
    got = import_probe(**{var: junk})
    assert (got["port"], got["lanes"], got["wait"]) == (9222, 3, 240.0)


def test_documented_defaults_apply_when_nothing_is_set():
    got = import_probe()
    assert (got["port"], got["lanes"], got["wait"]) == (9222, 3, 240.0)
    assert got["extract_debug"] is False
    assert got["bridge_debug"] is False


def test_captcha_wait_reads_as_a_float():
    """The budget at the captcha gate adds it to a float, so the type has to
    survive the coercion and not just compare equal to one."""
    got = import_probe(MOON_DN_CAPTCHA_WAIT="30")
    assert got["wait"] == 30.0
    assert isinstance(got["wait"], float)


@pytest.mark.parametrize("port,lanes,wait",
                         [(9222, 1, 0), (9333, 8, 30), (1024, 4, 600), (65535, 8, 1)])
def test_valid_values_are_honoured_exactly(port, lanes, wait):
    got = import_probe(MOON_CDP_PORT=str(port), MOON_DN_LANES=str(lanes),
                       MOON_DN_CAPTCHA_WAIT=str(wait))
    assert (got["port"], got["lanes"], got["wait"]) == (port, lanes, float(wait))


# ── out of range: clamped into the documented range, not handed to Chrome ──

@pytest.mark.parametrize("raw,expected",
                         [("0", 1), ("-5", 1), ("65535", 65535),
                          ("65536", 65535), ("99999", 65535)])
def test_cdp_port_stays_a_real_port(raw, expected):
    assert import_probe(MOON_CDP_PORT=raw)["port"] == expected


@pytest.mark.parametrize("raw,expected",
                         [("0", 1), ("-1", 1), ("1", 1), ("4", 4), ("8", 8),
                          ("9", 8), ("99", 8)])
def test_lanes_respect_the_documented_1_to_8_range(raw, expected):
    assert import_probe(MOON_DN_LANES=raw)["lanes"] == expected


@pytest.mark.parametrize("raw,expected", [("-1", 0.0), ("-240", 0.0), ("0", 0.0)])
def test_captcha_wait_cannot_go_negative(raw, expected):
    assert import_probe(MOON_DN_CAPTCHA_WAIT=raw)["wait"] == expected


# ── MOON_DEBUG: docs/CONFIGURATION.md says "1 = trace every gate" ─────────────
# Read as bool(get(...)) it was on for every non-empty value, MOON_DEBUG=0
# included -- so switching it off switched it on.

@pytest.mark.parametrize("value,expected",
                         [("1", True), ("2", True), ("true", True), ("on", True),
                          ("yes", True), ("TRUE", True), ("On", True), (" 1 ", True),
                          ("0", False), ("false", False), ("no", False),
                          ("off", False), ("", False), ("FALSE", False),
                          ("OFF", False), ("No", False), (" 0 ", False), ("  ", False)])
def test_moon_debug_one_is_on_and_zero_is_off(value, expected):
    got = import_probe(MOON_DEBUG=value)
    assert got["extract_debug"] is expected
    assert got["bridge_debug"] is expected, "the GUI host reads it its own way"


def test_moon_debug_is_off_when_unset():
    got = import_probe()
    assert got["extract_debug"] is False and got["bridge_debug"] is False


# ── _num itself ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", " ", "abc", [], 3.5 + 0j])
def test_num_falls_back_to_the_default_for_anything_that_will_not_cast(raw):
    assert moon_extract._num(raw, "9222") == 9222


@pytest.mark.parametrize("raw", [9222, "9222", " 9222 ", "+9222"])
def test_num_casts_what_it_can(raw):
    assert moon_extract._num(raw, "3") == 9222


def test_num_clamps_into_the_documented_range():
    assert moon_extract._num("0", "9222", lo=1, hi=65535) == 1
    assert moon_extract._num("99999", "9222", lo=1, hi=65535) == 65535
    assert moon_extract._num("9333", "9222", lo=1, hi=65535) == 9333
    assert moon_extract._num("junk", "9222", lo=1, hi=65535) == 9222


def test_num_keeps_the_requested_type_through_a_clamp():
    """DN_MANUAL_CAPTCHA_TIMEOUT is summed with floats, so a clamped 0 is 0.0."""
    value = moon_extract._num("-5", "240", cast=float, lo=0)
    assert value == 0.0
    assert isinstance(value, float)


def test_num_survives_a_default_that_will_not_cast_either():
    assert moon_extract._num(None, "not-a-number") == 0
    assert moon_extract._num("junk", "not-a-number") == 0


# ── configure(): the same coercion, for the values the GUI supplies ──────────

CONFIG_GLOBALS = ("DN_LANES", "CHROME_PATH", "DN_API_KEY",
                  "DN_MANUAL_CAPTCHA_TIMEOUT", "DN_HEADLESS")


@pytest.fixture
def extract():
    """configure() writes module globals, so put them back afterwards."""
    saved = {name: getattr(moon_extract, name) for name in CONFIG_GLOBALS}
    try:
        yield moon_extract
    finally:
        for name, value in saved.items():
            setattr(moon_extract, name, value)


def test_configure_applies_what_it_is_given(extract):
    applied = extract.configure(lanes=5, captcha_wait=45)
    assert extract.DN_LANES == 5
    assert extract.DN_MANUAL_CAPTCHA_TIMEOUT == 45.0
    assert applied["lanes"] == 5
    assert applied["captcha_wait"] == 45


@pytest.mark.parametrize("given,expected", [(0, 1), (-3, 1), (1, 1), (8, 8), (9, 8), (99, 8)])
def test_configure_clamps_lanes(given, expected):
    extract = moon_extract
    saved = extract.DN_LANES
    try:
        extract.configure(lanes=given)
        assert extract.DN_LANES == expected
    finally:
        extract.DN_LANES = saved


@pytest.mark.parametrize("given,expected", [(-1, 0.0), (-240, 0.0), (0, 0.0), (30, 30.0)])
def test_configure_keeps_the_captcha_wait_non_negative(given, expected):
    extract = moon_extract
    saved = extract.DN_MANUAL_CAPTCHA_TIMEOUT
    try:
        extract.configure(captcha_wait=given)
        assert extract.DN_MANUAL_CAPTCHA_TIMEOUT == expected
    finally:
        extract.DN_MANUAL_CAPTCHA_TIMEOUT = saved


def test_configure_keeps_the_current_setting_for_junk(extract):
    """The GUI cannot send junk here (both inputs are range sliders, and a range
    input clamps its own value), so this is hardening -- but it is the same
    coercion, and it is what keeps a bad settings.json from stopping a run."""
    extract.configure(lanes=6, captcha_wait=90)
    extract.configure(lanes="abc", captcha_wait="")
    assert extract.DN_LANES == 6
    assert extract.DN_MANUAL_CAPTCHA_TIMEOUT == 90.0


def test_configure_keeps_a_fractional_captcha_wait(extract):
    """int() before float() used to floor 2.5 to 2.0."""
    extract.configure(captcha_wait=2.5)
    assert extract.DN_MANUAL_CAPTCHA_TIMEOUT == 2.5


def test_configure_leaves_untouched_settings_alone(extract):
    extract.configure(lanes=4, chrome_path="/x/chrome", api_key="k", captcha_wait=30)
    before = (extract.CHROME_PATH, extract.DN_API_KEY, extract.DN_MANUAL_CAPTCHA_TIMEOUT)
    extract.configure(lanes=2)
    assert (extract.CHROME_PATH, extract.DN_API_KEY, extract.DN_MANUAL_CAPTCHA_TIMEOUT) == before
