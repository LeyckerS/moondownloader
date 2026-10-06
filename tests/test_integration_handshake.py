"""The integration harnesses' boot handshake must accept the app's version.

Both ``integration_http.py`` and ``integration_web.py`` wait for the page's
boot sequence to stamp the ``#version`` line. They once waited for a literal
``includes('v2')`` -- true while the app was v2.x, then a permanent 8s timeout
on every run once the app shipped v4.2 (so neither harness could ever reach
its own first checkpoint). This suite pins the new contract: the predicate is
a version-agnostic ``startsWith('v')`` that the version the app actually ships
satisfies.
"""

import integration_http
import integration_web
from moon_download import VERSION


def test_boot_handshake_no_longer_requires_the_literal_v2_string():
    for js in (integration_http.VERSION_HANDSHAKE_JS,
               integration_web.VERSION_HANDSHAKE_JS):
        assert "includes('v2')" not in js
        assert "textContent.startsWith('v')" in js


def test_shipped_version_satisfies_the_boot_handshake():
    # The app stamps #version with VERSION (plain, or " · preview" from the
    # MockApi in web/index.html). Both satisfy startsWith('v'); neither
    # satisfies the old includes('v2') -- the mismatch that rotted the
    # harnesses after the v2.x era.
    for text in (VERSION, f"{VERSION} · preview"):
        assert text.startswith("v"), f"{text!r} must pass the handshake"
        assert "v2" not in text, f"{text!r} contains the dead 'v2' fragment"
