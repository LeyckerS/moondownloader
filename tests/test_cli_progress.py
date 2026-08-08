from __future__ import annotations

import time

import moon_cli
from moon_engine import Engine


def test_cli_progress_line_reports_extraction_stage():
    engine = Engine()
    engine.begin_external_progress(5)
    try:
        first = engine.snapshot()
        line = moon_cli._progress_line(first, 5)
        assert "extracting 0/5" in line
        assert "done" not in line
        time.sleep(0.1)
        assert moon_cli._progress_key(first) == moon_cli._progress_key(engine.snapshot())
    finally:
        engine.finish_external_progress()


def test_cli_progress_line_switches_to_downloading_after_extraction():
    engine = Engine()
    engine.begin_external_progress(2)
    try:
        engine.mark_extraction("https://example.test/one", True)
        engine.mark_extraction("https://example.test/two", True)
        engine.mark_download_start()
        now = time.monotonic()
        engine.progress_bytes().extend([(now - 0.2, 1_000_000), (now - 0.1, 1_000_000)])

        snapshot = engine.snapshot()
        line = moon_cli._progress_line(snapshot, 2)
        assert "downloading 0/2" in line
        assert "MB/s" in line or "KB/s" in line
        assert snapshot["metrics"]["speed_mbs"] > 0
    finally:
        engine.finish_external_progress()


def test_external_progress_counts_re_extraction_once():
    engine = Engine()
    url = "https://example.test/retried"
    engine.begin_external_progress(1)
    try:
        engine.mark_extraction(url, True)
        engine.mark_extraction(url, True)

        metrics = engine.snapshot()["metrics"]
        assert metrics["extract_done"] == 1
        assert metrics["extract_total"] == 1
    finally:
        engine.finish_external_progress()
