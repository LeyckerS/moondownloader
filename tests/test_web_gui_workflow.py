"""Test: the web workflow actually runs render_gui.py, and still will.

Regression test for #179. `web-syntax-check.yml` only ran `node --check
web/app.js`, which proves the file parses and nothing else -- a TypeError thrown
while the page fills, an element wider than the viewport or a horizontal
scrollbar all pass it. `render_gui.py` catches all of those in headless
Chromium, so the wiring is what makes the check worth having.

Two things then rot quietly. Someone deletes or renames the job and CI stays
green, because nothing asserts the job exists. Or the paths filter keeps
`web/**` only, so a change to `render_gui.py` itself -- the thing that decides
what CI enforces -- no longer triggers the check that enforces it, which is the
same shape as #164.

The workflow is read as text rather than parsed. That is not laziness: this file
runs in the `no-chrome` job, which installs only aiohttp, curl_cffi and pytest,
so PyYAML is not available and adding it would change the dependency contract.
It is also stricter where it counts. A YAML parser is happy with steps in any
order, and step order is the thing that breaks a CI job -- Chromium installed
after the render, or the upload that keeps no screenshot on a failure. So the
steps are read positionally and compared by index.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "web-syntax-check.yml"

TRIGGERS = ("push", "pull_request")

STEP_START = re.compile(r"^      - name: (?P<name>.+)$")
JOB_KEY = re.compile(r"^  [A-Za-z0-9_-]+:$")
# A key of a step, at the step's own indentation. Comment lines start with "#"
# at the shallower `      #`, so they cannot be mistaken for keys.
STEP_KEY = re.compile(r"^        (?P<key>[A-Za-z][A-Za-z0-9_-]*):[ \t]*(?P<value>.*?)[ \t]*$")
NESTED_KEY = re.compile(r"^          (?P<key>[A-Za-z][A-Za-z0-9_-]*):[ \t]*(?P<value>.*?)[ \t]*$")


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _strip_quotes(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" \
        else value


def _job_body(job: str) -> str:
    """The text of one job, from its key to the next job key or end of file."""
    text = _text()
    start = re.search(rf"^  {re.escape(job)}:$", text, re.MULTILINE)
    assert start, f"no {job} job in {WORKFLOW.name}"
    rest = text[start.end():]
    nxt = JOB_KEY.search(rest, re.MULTILINE)
    return rest[:nxt.start()] if nxt else rest


def _steps() -> list[dict[str, str]]:
    """Each step of the render-gui job, in the order the file lists them.

    Keys are read positionally: `uses`, `run` and `if` belong to the step, and
    `path`/`key` are read from the block under `with:`.
    """
    steps: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_with = False
    for line in _job_body("render-gui").splitlines():
        head = STEP_START.match(line)
        if head:
            current = {"name": head.group("name").strip()}
            steps.append(current)
            in_with = False
            continue
        if current is None:
            continue
        if line.strip() == "with:":
            in_with = True
            continue
        key = STEP_KEY.match(line)
        if key:
            in_with = False
            current[key.group("key")] = _strip_quotes(key.group("value"))
            continue
        nested = NESTED_KEY.match(line)
        if nested and in_with:
            current[f"with.{nested.group('key')}"] = _strip_quotes(nested.group("value"))
    return steps


def _step(pattern: str) -> dict[str, str]:
    for step in _steps():
        if re.search(pattern, step.get("run", "") + " " + step.get("uses", "")):
            return step
    raise AssertionError(f"no step matching {pattern!r} in the render-gui job")


def _trigger_paths(trigger: str) -> list[str]:
    """The `paths:` entries under one trigger, as written."""
    text = _text()
    start = re.search(rf"^  {re.escape(trigger)}:$", text, re.MULTILINE)
    assert start, f"no {trigger} trigger"
    rest = text[start.end():]
    end = re.search(r"^  [A-Za-z0-9_-]+:$", rest, re.MULTILINE)
    block = rest[:end.start()] if end else rest
    paths_at = re.search(r"^    paths:$", block, re.MULTILINE)
    assert paths_at, f"{trigger} has no paths filter"
    out: list[str] = []
    for line in block[paths_at.end():].splitlines():
        entry = re.match(r'^      - (".*"|\'.*\'|\S+)$', line)
        if entry:
            out.append(_strip_quotes(entry.group(1)))
        elif line.strip():
            break
    return out


# --- the job exists and does the work ---------------------------------------


def test_the_render_job_exists_and_runs_render_gui():
    step = _step(r"render_gui\.py")
    assert step.get("run"), "the step invoking render_gui.py has no run: line"
    assert step["run"].startswith("python "), (
        f"expected the module to be run as `python ...`, got {step['run']!r}"
    )


def test_chromium_is_installed_before_the_render_runs():
    steps = _steps()
    install = next(i for i, s in enumerate(steps) if "playwright install" in s.get("run", ""))
    render = next(i for i, s in enumerate(steps) if "render_gui.py" in s.get("run", ""))
    assert "chromium" in steps[install]["run"], (
        f"installs a browser but not Chromium: {steps[install]['run']!r}"
    )
    assert install < render, (
        "Chromium is installed at step "
        f"{install}, after the render at step {render}"
    )


def test_the_render_writes_into_the_directory_that_gets_uploaded():
    steps = _steps()
    render = next(s for s in steps if "render_gui.py" in s.get("run", ""))
    upload = next(s for s in steps if "actions/upload-artifact" in s.get("uses", ""))

    out_dir = render["run"].split()[-1]
    uploaded = upload.get("with.path", "")
    assert uploaded, "the upload step has no path:"
    assert uploaded.rstrip("/") == out_dir, (
        f"render writes {out_dir!r} but the upload collects {uploaded!r}, "
        "so a failing run would attach nothing"
    )


def test_the_upload_is_guarded_so_a_failure_still_keeps_the_picture():
    upload = next(s for s in _steps() if "actions/upload-artifact" in s.get("uses", ""))
    assert upload.get("if", "").replace(" ", "") == "always()", (
        f"the upload is not guarded by `if: always()` (if={upload.get('if')!r}), "
        "so a failing run keeps no screenshot"
    )


# --- the cost of the job ----------------------------------------------------


def test_chromium_is_cached_from_the_linux_path_and_the_dependency_files():
    steps = _steps()
    cache = next(s for s in steps if "actions/cache" in s.get("uses", ""))
    assert cache.get("with.path") == "~/.cache/ms-playwright", (
        f"caches {cache.get('with.path')!r}; on a Linux runner that path is "
        "~/.cache/ms-playwright, so this would never hit"
    )
    key = cache.get("with.key", "")
    for f in ("requirements.txt", "constraints.txt"):
        assert f in key, f"the cache key ignores {f}: {key!r}"


# --- the check keeps being triggered ----------------------------------------


def test_every_trigger_watches_the_files_the_check_depends_on():
    # Same reasoning as #164: a file that decides what CI enforces has to be
    # able to trigger it, or the check can be broken without CI noticing.
    for trigger in TRIGGERS:
        paths = _trigger_paths(trigger)
        for required in ("web/**", "render_gui.py",
                         "requirements.txt", "constraints.txt"):
            assert required in paths, (
                f"{trigger} does not watch {required}; paths={paths}"
            )


def test_the_javascript_syntax_job_is_left_alone():
    text = _text()
    assert "javascript-syntax:" in text, "the syntax job was removed"
    assert "run: node --check web/app.js" in text, "the syntax check was changed"
