"""Tests for moon_cli.py exit codes (Issue #32).

Ensures the CLI correctly maps outcomes and errors to structured exit codes.
"""
import sys
import pytest
from unittest.mock import patch

import moon_cli

def test_exit_code_2_argparse_error():
    # argparse raises SystemExit(2) natively when args are missing/invalid
    with patch("sys.argv", ["moon_cli.py"]):
        with pytest.raises(SystemExit) as exc:
            moon_cli.main()
        assert exc.value.code == 2

def test_exit_code_2_missing_urls_file():
    with patch("sys.argv", ["moon_cli.py", "--urls", "does_not_exist.txt", "--output", "./out"]):
        with pytest.raises(SystemExit) as exc:
            moon_cli.main()
        assert exc.value.code == 2

def test_exit_code_2_empty_urls_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("   \n# commented line\n\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with pytest.raises(SystemExit) as exc:
            moon_cli.main()
        assert exc.value.code == 2

def test_exit_code_0_success(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("http://example.com/file.zip\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with patch("moon_cli.run", return_value=(1, 0, False)): # (ok, fail, aborted)
            with pytest.raises(SystemExit) as exc:
                moon_cli.main()
            assert exc.value.code == 0

def test_exit_code_1_partial_failure(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("http://example.com/1.zip\nhttp://example.com/2.zip\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with patch("moon_cli.run", return_value=(1, 1, False)): # (ok, fail, aborted)
            with pytest.raises(SystemExit) as exc:
                moon_cli.main()
            assert exc.value.code == 1

def test_exit_code_3_total_failure(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("http://example.com/file.zip\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with patch("moon_cli.run", return_value=(0, 1, False)): # (ok, fail, aborted)
            with pytest.raises(SystemExit) as exc:
                moon_cli.main()
            assert exc.value.code == 3

def test_exit_code_1_keyboard_interrupt(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("http://example.com/file.zip\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with patch("moon_cli.run", side_effect=KeyboardInterrupt()):
            with pytest.raises(SystemExit) as exc:
                moon_cli.main()
            assert exc.value.code == 1

def test_exit_code_1_aborted(tmp_path):
    f = tmp_path / "links.txt"
    f.write_text("http://example.com/file.zip\n", encoding="utf-8")
    with patch("sys.argv", ["moon_cli.py", "--urls", str(f), "--output", "./out"]):
        with patch("moon_cli.run", return_value=(0, 1, True)): # aborted (e.g. ENOSPC)
            with pytest.raises(SystemExit) as exc:
                moon_cli.main()
            assert exc.value.code == 1
