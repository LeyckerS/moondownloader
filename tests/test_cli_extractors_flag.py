import sys
import moon_cli


def _run(monkeypatch, capsys, *extra):
    monkeypatch.setattr(sys, "argv", ["moon_cli.py", "--urls", "missing.txt", "--output", "out", *extra])
    try:
        moon_cli.main()
    except SystemExit:
        pass
    return capsys.readouterr().out


def test_extractors_is_primary(monkeypatch, capsys):
    assert "deprecated" not in _run(monkeypatch, capsys, "--extractors", "11")


def test_browsers_alias_warns(monkeypatch, capsys):
    assert "--browsers is deprecated; use --extractors instead" in _run(monkeypatch, capsys, "--browsers", "7")


def test_extractors_wins(monkeypatch, capsys):
    assert "ignored because --extractors was also provided" in _run(monkeypatch, capsys, "--browsers", "7", "--extractors", "11")


def test_help_hides_browsers(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["moon_cli.py", "--help"])
    try:
        moon_cli.main()
    except SystemExit:
        pass
    output = capsys.readouterr().out
    assert "--extractors" in output
    assert "--browsers" not in output
