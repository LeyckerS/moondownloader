import pytest
from moon_download import ProxyPool, parse_proxy_line


def test_parse_proxy_line_builds_proxy_auth_header():
    cfg = parse_proxy_line("1.2.3.4:8080:user:pass")
    assert cfg is not None
    assert cfg["url"] == "http://1.2.3.4:8080"
    assert cfg["auth"] == "Basic dXNlcjpwYXNz"


def test_parse_proxy_line_builds_auth_header_for_credentials_first():
    cfg = parse_proxy_line("user:pass:5.6.7.8:9090")
    assert cfg is not None
    assert cfg["url"] == "http://5.6.7.8:9090"
    assert cfg["auth"] == "Basic dXNlcjpwYXNz"


def test_parse_proxy_line_no_auth_for_two_part_line():
    cfg = parse_proxy_line("5.6.7.8:9090")
    assert cfg is not None
    assert cfg["url"] == "http://5.6.7.8:9090"
    assert cfg["auth"] is None


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

def test_zero_parsed_proxies(capsys, tmp_path):
    """A file that exists but parses to nothing is the case #42 was really about."""
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("invalid_line\njust_a_word\n")
    pool = ProxyPool()
    loaded, skipped = pool.load(str(proxy_file), is_default=True)
    assert loaded == 0
    assert skipped == 2
    captured = capsys.readouterr()
    assert "yielded 0 proxies" in captured.out
