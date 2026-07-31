import pytest
from moon_download import ProxyPool


def test_proxy_pool_load_valid(tmp_path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("1.2.3.4:8080\nuser:pass:5.6.7.8:9090\n# comment\n\n")
    pool = ProxyPool()
    loaded, skipped = pool.load(str(proxy_file))
    assert loaded == 2
    assert skipped == 0
    assert len(pool.proxies) == 2


def test_proxy_pool_load_skipped(tmp_path):
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("1.2.3.4:8080\ninvalid_line\n5.6.7.8:9090\n")
    pool = ProxyPool()
    loaded, skipped = pool.load(str(proxy_file))
    assert loaded == 2
    assert skipped == 1


def test_missing_explicit_proxy(capsys):
    pool = ProxyPool()
    loaded, skipped = pool.load("non_existent_file.txt", is_default=False)
    assert loaded == 0
    assert skipped == 0
    captured = capsys.readouterr()
    assert "WARNING: proxy file not found" in captured.out


def test_missing_default_proxy(capsys):
    pool = ProxyPool()
    loaded, skipped = pool.load("non_existent_file.txt", is_default=True)
    assert loaded == 0
    assert skipped == 0
    captured = capsys.readouterr()
    assert "WARNING: proxy file not found" not in captured.out
