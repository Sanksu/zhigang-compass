# -*- coding: utf-8 -*-
"""Abstract base for pilot official-career-site adapters.
All adapters live under this Pilot20/adapters directory only; never
copied/moved into the zhigang-compass production code during this Pilot.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

BJ = timezone(timedelta(hours=8))


def bj_now_iso() -> str:
    return datetime.now(BJ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def sha256_hex(responsibilities: str, requirements: str) -> str:
    resp = (responsibilities or "").strip()
    req = (requirements or "").strip()
    payload = (resp + "\n" + req).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def simhash64(text: str) -> int:
    """Pure-python 64-bit SimHash on 3-char shingles for Chinese/English
    text. Pilot-internal near-dupe heuristic only.
    """
    t = (text or "").strip()
    if not t:
        return 0
    joined = "".join(re.split(r"\s+", re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]+", " ", t).strip()))
    if len(joined) < 3:
        joined = t
    shingles: List[str] = []
    if len(joined) <= 3:
        shingles = [joined]
    else:
        for i in range(len(joined) - 2):
            shingles.append(joined[i:i + 3])
    v = [0] * 64
    for sh in shingles:
        h = hashlib.md5(sh.encode("utf-8")).digest()
        hv = int.from_bytes(h[:8], "little", signed=False) ^ int.from_bytes(h[8:], "little", signed=False)
        for i in range(64):
            bit = (hv >> i) & 1
            v[i] += 1 if bit else -1
    result = 0
    for i in range(64):
        if v[i] > 0:
            result |= (1 << i)
    return result


def hamming64(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def normalise_company(name):
    if not name:
        return ""
    n = name.strip().lower()
    aliases = [
        (["腾讯", "tencent", "腾讯科技", "腾讯公司", "腾讯云"], "tencent"),
        (["字节跳动", "bytedance", "字节", "抖音集团", "toutiao", "今日头条"], "bytedance"),
        (["滴滴", "didiglobal", "didi", "滴滴出行"], "didiglobal"),
        (["阿里巴巴", "alibaba", "阿里"], "alibaba"),
        (["百度", "baidu"], "baidu"),
        (["美团", "meituan"], "meituan"),
        (["智联", "zhaopin", "zhilian", "智联招聘"], "zhilian"),
    ]
    for keys, canon in aliases:
        for k in keys:
            if k in n:
                return canon
    n = re.sub(r"[^0-9a-z\u4e00-\u9fa5]+", "", n)
    return n


def normalise_title(title):
    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"[^0-9a-z\u4e00-\u9fa5]+", "", t)
    return t


def normalise_location(loc):
    if not loc:
        return ""
    l = loc.strip()
    for sep in ["/", "、", "·", ",", "，", "\\"]:
        if sep in l:
            l = l.split(sep, 1)[0]
    return re.sub(r"\s+", "", l)


STANDARD_FIELDS = [
    "source", "source_company", "source_id", "source_id_method", "source_url",
    "job_title_raw", "company_name", "location", "salary", "source_education",
    "source_experience", "publish_time", "responsibilities", "requirements",
    "detail_raw_text", "crawl_time", "_sha256",
]


@dataclass
class ParseResult:
    record = None
    accessible_ok: bool = True
    login_wall: bool = False
    captcha: bool = False
    access_verification: bool = False
    http_or_struct_error = None
    detail_raw_text_len: int = 0
    adapter_name: str = ""
    adapter_parse_success: bool = False


class BaseCareerAdapter:
    adapter_name: str = "base"
    source_company_canonical: str = ""
    source_id_method_default: str = ""

    def match(self, url: str) -> bool:
        raise NotImplementedError

    def build_detail_url(self, stable_id: str):
        return None

    def parse(self, url, snapshot_text):
        raise NotImplementedError
