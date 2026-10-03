"""Regression coverage for same-tab redirects in the datanodes browser flow."""
from __future__ import annotations

import moon_extract


LANDING_HOST = "datanodes.to"
SELF_URLS = frozenset({"https://datanodes.to/download"})


def blocks(url: str, *, is_navigation: bool = True, is_main_frame: bool = True) -> bool:
    return moon_extract.dn_should_block_external_navigation(
        url,
        LANDING_HOST,
        None,
        SELF_URLS,
        is_navigation=is_navigation,
        is_main_frame=is_main_frame,
    )


def test_blocks_unrecognized_top_level_external_navigation():
    assert blocks("https://ads.example/landing")


def test_allows_datanodes_and_cloudflare_navigations():
    assert not blocks("https://datanodes.to/download/step-2")
    assert not blocks("https://www.datanodes.to/download/step-2")
    assert not blocks("https://challenges.cloudflare.com/cdn-cgi/challenge")
    assert not blocks("https://abc.challenges.cloudflare.com/cdn-cgi/challenge")


def test_allows_recognized_direct_file_handoff():
    file_url = "https://s1.datanodes.to/d/123456789/pack.part01.rar?token=long-token-value"

    assert not blocks(file_url)


def test_leaves_subresources_and_child_frame_navigations_alone():
    external = "https://ads.example/landing"

    assert not blocks(external, is_navigation=False)
    assert not blocks(external, is_main_frame=False)


def test_setting_can_disable_the_navigation_filter(monkeypatch):
    monkeypatch.setattr(moon_extract, "DN_BLOCK_SAME_TAB_SPAM", False)

    assert not blocks("https://ads.example/landing")


def test_relative_or_hostless_urls_are_not_blocked():
    assert not blocks("about:blank")
