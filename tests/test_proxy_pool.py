import pytest
from moon_download import ProxyPool, count_usable_proxies, parse_proxy_line


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


# ---------------------------------------------------------------------------
# Lines that name no usable host and port must not be counted as loaded.
# ---------------------------------------------------------------------------

# Every one of these names no usable host and port, so none may be loaded.
UNUSABLE_LINES = [
    "1.2.3.4:notaport",      # the port is not a number
    "1.2.3.4:abcde",        # ... and it is short enough to slip a lax range check
    "1.2.3.4:80a",
    "1.2.3.4: 80",          # a space after the colon; int() would accept it, a port may not
    "1.2.3.4:99999",         # above the TCP range
    "1.2.3.4:0",             # below the TCP range
    "1.2.3.4:-1",
    "1.2.3.4:",              # empty port
    "1.2.3.4:notaport:user:pass",   # bad port in ip:port:user:pass order
    "user:pass:1.2.3.4:notaport",   # bad port in user:pass:ip:port order
    "username:password",     # credentials without a host and port
    "not a proxy:8080",      # a host may not contain whitespace
    "socks",                 # a scheme word, not a URL
    "http://",
    "https://",
    "sockswhatever",
    "1.2.3.4:8080:user:pa:ss",  # a password containing a colon
]

# Every one of these is a proxy line a user could reasonably write.
USABLE_LINES = [
    "1.2.3.4:8080",
    "proxy.example.com:8080",
    "1.2.3.4:8080:user:pass",
    "user:pass:1.2.3.4:8080",
    "http://1.2.3.4:8080",
    "https://1.2.3.4:8080",
    "socks5://1.2.3.4:1080",
    "socks5h://host.example:1080",
    "http://proxy.example.com",   # no port: http defaults to 80
    "http://[::1]:8080",          # IPv6 literal
]


@pytest.mark.parametrize("line", UNUSABLE_LINES)
def test_unusable_lines_are_not_parsed(line):
    assert parse_proxy_line(line) is None


@pytest.mark.parametrize("line", USABLE_LINES)
def test_usable_lines_are_still_parsed(line):
    assert parse_proxy_line(line) is not None


def test_unusable_lines_are_counted_as_skipped(tmp_path):
    """A typo'd list must not inflate the "loaded" tally the CLI prints."""
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text("\n".join(USABLE_LINES + UNUSABLE_LINES) + "\n")

    pool = ProxyPool()
    loaded, skipped = pool.load(str(proxy_file))
    assert loaded == len(USABLE_LINES)
    assert skipped == len(UNUSABLE_LINES)
    assert [entry["url"] for entry in pool.proxies] == [
        parse_proxy_line(line)["url"] for line in USABLE_LINES
    ]

    assert count_usable_proxies(str(proxy_file)) == (
        len(USABLE_LINES),
        len(UNUSABLE_LINES),
    )


def test_credentials_survive_in_both_field_orders():
    """Validation must not cost a line its BasicAuth."""
    for line, login in (
        ("1.2.3.4:8080:user:pass", "user"),
        ("user:pass:1.2.3.4:8080", "user"),
    ):
        entry = parse_proxy_line(line)
        assert entry["url"] == "http://1.2.3.4:8080"
        assert entry["auth"].login == login
        assert entry["auth"].password == "pass"
