# -*- coding: utf-8 -*-
"""Simple router: chooses adapter by URL match.  Pilot-only."""
from .base import BaseCareerAdapter, ParseResult
from .tencent_adapter import TencentCareerAdapter
from .bytedance_adapter import BytedanceCareerAdapter


_REGISTERED = [TencentCareerAdapter(), BytedanceCareerAdapter()]


def pick_adapter(url: str) -> BaseCareerAdapter | None:
    for a in _REGISTERED:
        if a.match(url):
            return a
    return None


def parse_any(url: str, snapshot_text: str) -> ParseResult:
    a = pick_adapter(url)
    if not a:
        r = ParseResult()
        r.http_or_struct_error = "no registered adapter matches URL"
        return r
    return a.parse(url, snapshot_text)
