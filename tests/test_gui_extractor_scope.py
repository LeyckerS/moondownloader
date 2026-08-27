"""Regression coverage for the provider-specific extractor ceiling in the GUI."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")


def test_extractor_hint_has_a_dedicated_live_target():
    assert 'id="extractorScope"' in INDEX
    assert 'id="workers"' in INDEX
    assert 'id="pages"' in INDEX


def test_extractor_hint_reports_the_effective_datanodes_limit():
    assert "Math.min(workerCount, pageCount)" in APP
    assert 'T("extractors_hint", Math.min(workerCount, pageCount), pageCount)' in APP


def test_extractor_hint_tracks_both_controls_and_language_changes():
    assert '$("#workers").addEventListener("input", syncExtractorScope)' in APP
    assert '$("#pages").addEventListener("input", syncExtractorScope)' in APP
    assert APP.count("syncExtractorScope();") >= 2
    assert APP.count("extractors_hint:") == 2
