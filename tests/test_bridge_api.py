"""Tests for the loopback API boundary in moon_bridge.py.

The token is the only thing standing between a browser on the same machine and an
API that starts downloads and reads paths, so these drive a real `serve()` over a
real socket rather than calling the handler directly: the header value has to be
parsed off the wire and decoded before the auth check ever sees it, which is the
whole point of the non-ASCII case.

Chrome and the network are never involved -- `_Server` binds 127.0.0.1:0 and the
engine is a stand-in.
"""
from __future__ import annotations

import http.client
import json
import secrets
import socket

import pytest

import moon_bridge

TIMEOUT = 5.0


class FakeEngine:
    """Only what Api actually touches. `hello()` reads _cfg; snapshot can fail."""

    def __init__(self):
        self._cfg = {"out_folder": "/tmp/moon", "dn_chrome": "", "dn_apikey": ""}
        self.stopped = 0
        self.cleared = 0

    def snapshot(self, cursor):
        if cursor == -1:
            raise ValueError("bad cursor")
        return {"cursor": cursor}

    def start(self, cfg):
        return {"started": True, "got": cfg}

    def stop(self):
        self.stopped += 1
        return {"stopped": True}

    def clear_files(self):
        self.cleared += 1
        return {"cleared": True}


@pytest.fixture
def server():
    """A real loopback server on a kernel-chosen port, torn down afterwards."""
    api = moon_bridge.Api(FakeEngine(), dialogs=lambda kind: "")
    srv, _url = moon_bridge.serve(api)
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def duck_server():
    """A server whose api object carries a private callable.

    Api has no underscore-prefixed attribute today, so posting to /api/_<name>
    only ever hits the "no such attribute" path and never the guard that is
    actually there to stop it. A duck-typed object is the only way to reach it.
    """
    api = FakeEngine()
    api._secret = lambda: {"leaked": True}
    api.open_callable = lambda: {"ok": True}
    srv, _url = moon_bridge.serve(api)
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def token(server):
    return server.token


def call(server, name, args=(), token=None, method="POST"):
    """One request on its own connection. Returns (status, decoded body)."""
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-Moon-Token"] = token
        if method == "POST":
            conn.request("POST", f"/api/{name}", body=json.dumps({"args": list(args)}),
                         headers=headers)
        else:
            conn.request(method, f"/api/{name}", headers=headers)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"{}")
    finally:
        conn.close()


def raw_post(server, path: bytes, extra_headers: bytes = b"", token=None) -> int:
    """One request written straight onto the socket; returns the status code.

    Nothing validates the request on the way out, which is the point: header
    values and Content-Lengths that http.client would refuse to send are exactly
    the ones worth testing the server against.
    """
    host, port = server.server_address[:2]
    lines = [b"POST " + path + b" HTTP/1.1", b"Host: x", b"Connection: close"]
    if token is not None:
        lines.append(b"X-Moon-Token: " + token.encode("latin-1"))
    lines.append(extra_headers.rstrip(b"\r\n"))
    request = b"\r\n".join(lines) + b"\r\n\r\n"
    sock = socket.create_connection((host, port), timeout=TIMEOUT)
    try:
        sock.sendall(request)
        buf = b""
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    first = buf.split(b"\r\n", 1)[0].split()
    return int(first[1]) if len(first) > 1 and first[1].isdigit() else 0


# ── the token gate ───────────────────────────────────────────────────────────

def test_valid_token_is_accepted(server, token):
    status, body = call(server, "snapshot", [3], token)
    assert status == 200
    assert body == {"cursor": 3}


def test_wrong_token_is_forbidden(server, token):
    status, body = call(server, "snapshot", [0], "not-the-token")
    assert (status, body) == (403, {"error": "forbidden"})


def test_missing_token_is_forbidden(server):
    status, body = call(server, "snapshot", [0])
    assert (status, body) == (403, {"error": "forbidden"})


def test_empty_token_is_forbidden(server):
    status, body = call(server, "snapshot", [0], "")
    assert (status, body) == (403, {"error": "forbidden"})


# Header bytes arrive as latin-1, so every one of these is a value a client can
# genuinely put on the wire -- http.client encodes header values as latin-1 too,
# so each of these really does reach the server as a high byte. All of them are
# in U+0080-U+00FF for exactly that reason; nothing above U+00FF can be sent
# through this transport at all.
NON_ASCII_TOKENS = [
    "café",                      # 0xE9
    "\xff\xff\xff",              # high bytes, no ASCII lookalike
    "\x80",                      # first non-ASCII code point
    " ",                  # non-breaking space
    "«",                  # left guillemet
    "tokén",
    "ÿ" * 64,                    # long enough to matter for a length check
]


@pytest.mark.parametrize("bad", NON_ASCII_TOKENS)
def test_non_ascii_token_is_forbidden_not_dropped(server, bad):
    """A malformed token must be refused, not crash the handler.

    compare_digest raises TypeError on a non-ASCII str, and that exception used
    to escape _authorised() itself: no reply was ever written, the connection was
    torn down mid-request, and the traceback landed on stderr. The client saw
    RemoteDisconnected. It has to be an ordinary 403 like any other bad token.
    """
    status, body = call(server, "snapshot", [0], bad)
    assert status == 403, f"non-ASCII token {bad!r} did not get a 403"
    assert body == {"error": "forbidden"}


def test_non_ascii_token_is_refused_even_when_it_is_the_right_token(server, token):
    """Length is not what saves us: same length as the real token, one byte wrong."""
    corrupted = token[:-1] + "\xe9"
    assert len(corrupted) == len(token)
    assert corrupted != token
    status, body = call(server, "snapshot", [0], corrupted)
    assert (status, body) == (403, {"error": "forbidden"})


def test_minted_tokens_are_always_ascii(server):
    """The guard above is only sound because every token we mint is ASCII."""
    for _ in range(50):
        assert secrets.token_urlsafe(24).isascii()


def test_ascii_token_comparison_is_still_constant_time(server, monkeypatch):
    """The non-ASCII early return must not replace compare_digest."""
    seen = []
    real = secrets.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(moon_bridge.secrets, "compare_digest", spy)
    status, _ = call(server, "snapshot", [0], server.token)
    assert status == 200
    assert seen == [(server.token, server.token)]

    seen.clear()
    monkeypatch.setattr(moon_bridge.secrets, "compare_digest", spy)
    status, _ = call(server, "snapshot", [0], "café")
    assert status == 403
    assert seen == [], "a non-ASCII token must be rejected before any comparison"


# ── the connection has to survive an error reply ─────────────────────────────

def keepalive_call(server, name, args, tokens):
    """One connection, several sequential requests, each response fully read.

    `tokens` is one token per request, so a refused call can be followed by an
    accepted one on the very same connection.
    """
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    out = []
    try:
        for tok in tokens:
            headers = {"Content-Type": "application/json"}
            if tok is not None:
                headers["X-Moon-Token"] = tok
            conn.request("POST", f"/api/{name}", body=json.dumps({"args": list(args)}),
                         headers=headers)
            resp = conn.getresponse()
            out.append((resp.status, json.loads(resp.read() or b"{}")))
    finally:
        conn.close()
    return out


def test_forbidden_reply_leaves_the_connection_usable(server, token):
    """The body must be drained even when the answer is an error.

    HTTP/1.1 keeps the connection open, so replying 403 without reading the body
    leaves those bytes in the socket and the next request on the same connection
    is parsed starting in the middle of the JSON.
    """
    first, second = keepalive_call(server, "snapshot", [0], ["wrong-token", token])
    assert first == (403, {"error": "forbidden"})
    assert second == (200, {"cursor": 0}), "the connection was poisoned by the 403"


def test_not_found_reply_leaves_the_connection_usable(server, token):
    """Same drain requirement on the 404 path: unknown method, then a real one."""
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        conn.request("POST", "/api/no_such_method", body=json.dumps({"args": [0]}),
                     headers={"X-Moon-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 404
        assert "error" in json.loads(resp.read())

        conn.request("POST", "/api/snapshot", body=json.dumps({"args": [0]}),
                     headers={"X-Moon-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (200, {"cursor": 0})
    finally:
        conn.close()


def test_non_ascii_refusal_leaves_the_connection_usable(server, token):
    """The whole point: refused cleanly *and* the connection still works."""
    first, second = keepalive_call(server, "snapshot", [0], ["café", token])
    assert first == (403, {"error": "forbidden"})
    assert second == (200, {"cursor": 0})


def test_non_ascii_refusal_does_not_poison_a_following_refusal(server):
    """Two refusals in a row, then the real token -- nothing desynchronises."""
    results = keepalive_call(server, "snapshot", [0],
                             ["café", "ÿÿ", None, server.token])
    assert [status for status, _ in results] == [403, 403, 403, 200]


def test_a_body_survives_the_authorisation_check(server):
    """403 on a request that carried a real body, then the body is not re-read."""
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        big = json.dumps({"args": ["x" * 5000]})
        conn.request("POST", "/api/snapshot", body=big,
                     headers={"X-Moon-Token": "café", "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (403, {"error": "forbidden"})

        conn.request("POST", "/api/snapshot", body=json.dumps({"args": [1]}),
                     headers={"X-Moon-Token": server.token,
                              "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (200, {"cursor": 1})
    finally:
        conn.close()


# ── dispatch ─────────────────────────────────────────────────────────────────

def test_get_on_an_api_path_is_405(server, token):
    status, body = call(server, "snapshot", method="GET", token=token)
    assert status == 405
    assert body == {"error": "usa POST"}


def test_unknown_method_is_404(server, token):
    status, body = call(server, "definitely_not_a_method", [0], token)
    assert status == 404
    assert "definitely_not_a_method" in body["error"]


def test_private_methods_are_not_reachable(server, token):
    """A leading underscore is refused even though getattr finds it."""
    status, body = call(server, "_authorised", [0], token)
    assert status == 404


def test_private_callable_is_refused_even_when_getattr_finds_it(duck_server):
    """The guard, not the absence of the attribute, is what refuses this."""
    status, body = call(duck_server, "_secret", token=duck_server.token)
    assert status == 404, "a private callable must not be reachable over /api/"
    status, body = call(duck_server, "open_callable", token=duck_server.token)
    assert (status, body) == (200, {"ok": True}), "the guard must not eat public methods"


def test_non_api_post_is_404(server, token):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        conn.request("POST", "/not-api", body="{}", headers={"X-Moon-Token": token})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (404, {"error": "not found"})
    finally:
        conn.close()


def test_non_api_404_also_drains_the_body(server, token):
    """The 404 path owes the next request on the connection the same drain."""
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        conn.request("POST", "/not-api", body=json.dumps({"args": ["x" * 3000]}),
                     headers={"X-Moon-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (404, {"error": "not found"})

        conn.request("POST", "/api/snapshot", body=json.dumps({"args": [0]}),
                     headers={"X-Moon-Token": token, "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert (resp.status, json.loads(resp.read())) == (200, {"cursor": 0}), \
            "the undrained body desynchronised the next request"
    finally:
        conn.close()


def test_unparsable_content_length_is_not_a_crash(server, token):
    """A junk Content-Length must not take the handler down with it.

    "abc" and "1.5" hit the ValueError guard, "-1" hits the `> 0` guard. All of
    them must produce a real HTTP status rather than a dropped connection. (A
    huge-but-numeric length is deliberately not here: the server is then right to
    block waiting for a body that never arrives.)

    Raw sockets, because a Content-Length http.client considers malformed is
    exactly what is under test.
    """
    for bad in ("abc", "1.5", "-1"):
        raw_status = raw_post(server, b"/api/snapshot",
                              extra_headers=b"Content-Length: " + bad.encode() + b"\r\n",
                              token=token)
        assert raw_status == 200, (bad, raw_status)


# ── the Api contract: answer with {"error": ...}, never raise ────────────────

def test_method_errors_come_back_as_an_error_payload(server, token):
    status, body = call(server, "snapshot", [-1], token)
    assert status == 200, "the Api contract is a 200 carrying an error key"
    assert "snapshot" in body["error"]
    assert "bad cursor" in body["error"]


def test_non_numeric_cursor_is_an_error_payload_not_a_crash(server, token):
    """Api.snapshot coerces with int(), so a junk cursor fails before the engine."""
    status, body = call(server, "snapshot", ["boom"], token)
    assert status == 200
    assert "snapshot" in body["error"]


def test_malformed_json_is_an_error_payload_not_a_crash(server, token):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=TIMEOUT)
    try:
        for raw in (b"{not json", b"[]", b"null", b'"a string"', b"\xff\xfe"):
            conn.request("POST", "/api/snapshot", body=raw,
                         headers={"X-Moon-Token": token, "Content-Type": "application/json"})
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"{}")
            assert resp.status == 200, raw
            assert "error" in payload, f"{raw!r} produced {payload!r}"
    finally:
        conn.close()


def test_stop_and_clear_files_reach_the_engine(server, token):
    api = server.api
    assert call(server, "stop", token=token)[0] == 200
    assert call(server, "clear_files", token=token)[0] == 200
    assert (api.engine.stopped, api.engine.cleared) == (1, 1)
