"""Tests for render_gui.py's command-line argument handling.

The renderer is a CI quality gate (`web-syntax-check.yml` runs it on every web
change), so a command line it silently misreads is a gate that can turn green
without having rendered a single viewport.
"""

import sys

import render_gui


def test_odd_dimension_count_is_refused(tmp_path, monkeypatch, capsys):
    recorded: list[list] = []
    monkeypatch.setattr(render_gui, "shoot",
                        lambda out, sizes: recorded.append(list(sizes)) or 0)
    monkeypatch.setattr(sys, "argv", ["render_gui.py", str(tmp_path), "1440"])

    rc = render_gui.main()

    assert rc == 2, "an incomplete size list must be an error, not a silent exit 0"
    assert recorded == [], "shoot() must not run with an incomplete size list"
    assert "1440" in capsys.readouterr().err


def test_three_arguments_are_refused_too(tmp_path, monkeypatch, capsys):
    # A trailing size must not be silently dropped; any incomplete pair is an
    # error, whatever the count.
    recorded: list[list] = []
    monkeypatch.setattr(render_gui, "shoot",
                        lambda out, sizes: recorded.append(list(sizes)) or 0)
    monkeypatch.setattr(
        sys, "argv",
        ["render_gui.py", str(tmp_path), "1280", "720", "1920"],
    )

    rc = render_gui.main()

    assert rc == 2
    assert recorded == []
    assert "1280" in capsys.readouterr().err


def test_paired_dimensions_reach_shoot_unscathed(tmp_path, monkeypatch):
    recorded: list[list] = []
    monkeypatch.setattr(render_gui, "shoot",
                        lambda out, sizes: recorded.append(list(sizes)) or 0)
    monkeypatch.setattr(
        sys, "argv",
        ["render_gui.py", str(tmp_path), "1280", "720", "1920", "1080"],
    )

    rc = render_gui.main()

    assert rc == 0
    assert recorded == [[(1280, 720), (1920, 1080)]]


def test_no_size_arguments_use_the_default_viewports(tmp_path, monkeypatch):
    recorded: list[list] = []
    monkeypatch.setattr(render_gui, "shoot",
                        lambda out, sizes: recorded.append(list(sizes)) or 0)
    monkeypatch.setattr(sys, "argv", ["render_gui.py", str(tmp_path)])

    rc = render_gui.main()

    assert rc == 0
    assert recorded == [[(2554, 1400), (1440, 900)]]
