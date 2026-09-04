from __future__ import annotations

import time

import moon_cli
from moon_download import FileRecord
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
    first_record = FileRecord("https://example.test/one", "one")
    second_record = FileRecord("https://example.test/two", "two")
    engine.begin_external_progress(2)
    try:
        engine.mark_extraction(first_record, True)
        engine.mark_extraction(second_record, True)
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
    record = FileRecord("https://example.test/retried", "retried")
    engine.begin_external_progress(1)
    try:
        engine.mark_extraction(record, True)
        engine.mark_extraction(record, True)

        metrics = engine.snapshot()["metrics"]
        assert metrics["extract_done"] == 1
        assert metrics["extract_total"] == 1
    finally:
        engine.finish_external_progress()


def test_external_progress_counts_duplicate_url_records_independently():
    engine = Engine()
    url = "https://example.test/duplicate"
    first = FileRecord(url, "duplicate")
    second = FileRecord(url, "duplicate (2)")
    engine.begin_external_progress(2)
    try:
        assert engine.mark_extraction(first, False) is False
        assert engine.mark_extraction(second, False) is True

        metrics = engine.snapshot()["metrics"]
        assert metrics["extract_done"] == 2
        assert metrics["extract_total"] == 2
        assert metrics["dl_done"] == 2
        assert metrics["fail"] == 2
    finally:
        engine.finish_external_progress()
