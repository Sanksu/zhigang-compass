# -*- coding: utf-8 -*-
"""Tencent (careers.tencent.com) official career site adapter.

Pilot-only, lives in Pilot20/adapters/. Relies on the DOM-text snapshot
format returned by the browser snapshot tool: each visible text line is
rendered as a plain string often with surrounding `text "..." [refNN]`
envelopes or inline as `text "CSIG" [e24]` nodes.

The adapter is deliberately pattern-based rather than DOM-selector
based (we cannot load raw HTML in every case - the accessible tree
text is always available).
"""
from __future__ import annotations
import re
from typing import Dict, Any, Optional
from .base import (
    BaseCareerAdapter, ParseResult, bj_now_iso, sha256_hex, STANDARD_FIELDS
)


# Regex to unwrap quoted snapshot text.  Captures inner text.
# E.g. `text "岗位职责" [e183]` -> "岗位职责"
# Also supports leading/trailing `listitem "..." [eXX] (offscreen)?` etc.
_LINE_TEXT_RE = re.compile(
    r"""^\s*(?:listitem|text|heading|complementary|presentation)    \s*
         "((?:[^"\\]|\\.)*)"                                   \s*
         \[e[0-9]+\]                                             \s*
         (?:\(offscreen\))?                                     \s*
         $""",
    re.VERBOSE,
)
_REF_BARE = re.compile(r"\[e\d+\]")


def _unwrap_line(line: str) -> Optional[str]:
    """Extract pure visible text from a snapshot line, if it looks like
    a text/listitem/heading line, else return raw line.strip()."""
    line = line.rstrip()
    if not line:
        return None
    m = _LINE_TEXT_RE.match(line)
    if m:
        return m.group(1).encode().decode("unicode_escape") if "\\" in m.group(1) else m.group(1)
    # Strip trailing [ref] and (offscreen) for generic lines
    s = _REF_BARE.sub("", line).strip()
    if s.endswith("(offscreen)"):
        s = s[:-len("(offscreen)")].rstrip()
    # Remove known prefix tokens we don't need
    for prefix in ("text ", "listitem ", "heading ", "link ", "button ", "contentinfo ", "complementary ",
                   "presentation ", "banner ", "list ", "tree ", "textbox ", "main ", "article ",
                   "section ", "div ", "p ", "span ", "strong ", "em ", "li ", "ul ", "ol "):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    # Strip leading quotes if any
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s.strip() or None


def _extract_line_texts(snapshot_text: str):
    out = []
    for raw in (snapshot_text or "").splitlines():
        t = _unwrap_line(raw)
        if t:
            out.append(t)
    return out


POSTID_RE = re.compile(r"postId=(\d+)")


class TencentCareerAdapter(BaseCareerAdapter):
    adapter_name = "tencent_careers"
    source_company_canonical = "Tencent"
    source_id_method_default = "postId"

    REQ_LABELS = ("岗位要求", "任职要求", "任职资格", "职位要求", "岗位任职要求")
    RESP_LABELS = ("岗位职责", "职位描述", "岗位说明", "工作内容", "职位职责", "工作职责")

    def match(self, url: str) -> bool:
        return bool(url) and ("careers.tencent.com" in url or "tencent.com/jobdesc.html" in url)

    def _extract_postid(self, url: str) -> Optional[str]:
        m = POSTID_RE.search(url or "")
        return m.group(1) if m else None

    # --- Public API ----------------------------------------------------
    def build_detail_url(self, stable_id: str) -> Optional[str]:
        if not (stable_id or "").isdigit():
            return None
        return f"https://careers.tencent.com/jobdesc.html?postId={stable_id}"

    def parse(self, url, snapshot_text):
        result = ParseResult()
        result.adapter_name = self.adapter_name
        try:
            return self._parse_impl(url, snapshot_text, result)
        except Exception as exc:  # noqa: BLE001 - defensive, per repo rule no hard-fail
            result.http_or_struct_error = f"adapter_exception: {type(exc).__name__}: {str(exc)[:120]}"
            result.adapter_parse_success = False
            return result

    def _parse_impl(self, url, snapshot_text, result: ParseResult):
        # Check blocking conditions first from raw snapshot text
        low = (snapshot_text or "").lower()
        if any(k in low for k in ("登录墙", "login wall", "请登录后", "需登录", "需要登录", "captcha", "验证码", "人机验证", "访问验证")):
            if any(k in low for k in ("captcha", "验证码", "人机验证")):
                result.captcha = True
                result.http_or_struct_error = "captcha_detected"
            elif any(k in low for k in ("访问验证", "access verification")):
                result.access_verification = True
                result.http_or_struct_error = "access_verification"
            else:
                result.login_wall = True
                result.http_or_struct_error = "login_wall"
            result.accessible_ok = False
            return result

        postid = self._extract_postid(url)
        if not postid:
            result.http_or_struct_error = "postId missing from URL (unable to build stable source_id)"
            return result

        lines = _extract_line_texts(snapshot_text)
        if not lines:
            result.http_or_struct_error = "snapshot yielded zero visible text lines (page blank / DOM error)"
            return result

        # 1) title + location: first line that matches pattern `岗位名 [空格]+ 城市`
        title = None
        location = None
        # The common pattern line is "腾讯云DataBuddy -大模型算法专家 深圳"  (title + space + single chinese city)
        city_re = re.compile(r"^(?P<t>.+?)\s+(?P<loc>(?:北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|重庆|苏州|合肥|郑州|长沙|厦门|青岛|济南|天津|大连|宁波|无锡|东莞|佛山|珠海|贵阳|昆明|哈尔滨|福州|乌鲁木齐|兰州|南宁|南昌|沈阳|石家庄|长春|太原|海口|三亚|呼和浩特|西宁|银川|拉萨|香港|澳门|台北|多伦多|新加坡|东京|首尔|硅谷|西雅图|纽约|伦敦|巴黎|柏林|悉尼|墨尔本|越南)(?:[\s、/·,]|$))")
        # Fallback: line containing BG/careerline meta
        meta_re = re.compile(r"^(CSIG|CDG|IEG|WXG|PCG|TEG|S1|S2|S3|TME|PCG|QQ|OMG|MIG|SNG|FIT|FiT|TME)\b")
        publish_time = None
        experience = None

        # Find title/location candidate FIRST before any BG metadata line
        for i, l in enumerate(lines):
            if title is None:
                m = city_re.match(l)
                if m and len(m.group("t")) >= 4 and not meta_re.match(l):
                    # filter obvious non-titles: anything starting with a label word
                    t_cand = m.group("t").strip()
                    if not any(t_cand.startswith(w) for w in (
                        "岗位职责", "岗位要求", "职位描述", "任职要求", "团队介绍",
                        "加分项", "岗位亮点", "工作地点", "学历要求", "薪资",
                        "招聘类型", "产品与服务", "法律信息", "Copyright",
                    )):
                        title = t_cand
                        location = m.group("loc")
                        continue
            if meta_re.match(l):
                # Extract experience: "X 年以上工作经验" / "不限" / "应届毕业生"
                m2 = re.search(r"(\d+\s*年(?:以上|以下|左右)?(?:工作经验)?)|(不限)|(应届毕业生?)", l)
                if m2:
                    experience = m2.group(0)
                m3 = re.search(r"更新于(\d{4}年\d{1,2}月\d{1,2}日)", l)
                if m3:
                    publish_time = m3.group(1)
                break

        # Education (often within requirements)
        education = None

        # Split RESP / REQ blocks
        resp_start = req_start = extra_start = -1
        resp_label = req_label = None
        bonus_start = highlight_start = -1
        for i, l in enumerate(lines):
            s = l.strip()
            if resp_start < 0:
                for lab in self.RESP_LABELS:
                    if s == lab or s.startswith(lab) and len(s) < len(lab) + 6:
                        resp_start, resp_label = i, lab
                        break
            if req_start < 0:
                for lab in self.REQ_LABELS:
                    if s == lab or (s.startswith(lab) and len(s) < len(lab) + 6):
                        req_start, req_label = i, lab
                        break
            if bonus_start < 0 and ("加分项" in s or s == "加分项"):
                bonus_start = i
            if highlight_start < 0 and ("岗位亮点" in s or s == "岗位亮点"):
                highlight_start = i

        def block_from(start_idx: int, end_idx: int):
            """Collapse the text lines from start_idx..end_idx excl.
            Skip the label line at start_idx; join subsequent lines with '\n'.
            """
            if start_idx < 0:
                return ""
            end = end_idx if end_idx >= 0 else len(lines)
            pieces = []
            for j in range(start_idx + 1, min(end, len(lines))):
                s = lines[j].strip()
                if not s:
                    continue
                # Skip obviously unrelated footer lines (Copyright, 法律信息, etc.)
                if any(bad in s for bad in ("Copyright", "关注腾讯招聘", "关于腾讯", "腾讯公益", "客服中心", "法律信息", "服务协议", "隐私政策", "知识产权", "相关推荐岗位", "查看更多", "相关网站", "联系我们")):
                    break
                pieces.append(s)
            # Collapse long lines that sometimes contain 1. / 2. / 3. separated by large spaces into \n split
            joined = "\n".join(pieces).strip()
            # Break on regex `1.` `2.` etc. if they're run together without newline
            joined = re.sub(r"\s+(?=\d+\.)", "\n", joined)
            return joined.strip()

        # Determine end boundaries for each block
        def first_after(*candidates):
            xs = [c for c in candidates if c > 0]
            return min(xs) if xs else -1

        resp_end = first_after(req_start, bonus_start, highlight_start)
        req_end = first_after(bonus_start, highlight_start)

        responsibilities = block_from(resp_start, resp_end)
        requirements = block_from(req_start, req_end)

        # Education detection from requirements (passive, only if page-writte)
        if requirements:
            em = re.search(
                r"(博士|硕士(?:及以上)?|研究生|本科(?:及以上)?|大专|专科|中专|高中|初中|"
                r"Ph\.?D|PhD|Master|Bachelor|MBA)",
                requirements,
                flags=re.IGNORECASE,
            )
            if em:
                education = em.group(0)

        # Build detail_raw_text: company header line + meta + resp blocks
        # First locate the header lines we can display as-is (title if found + BG meta)
        header_meta_lines = []
        for l in lines[:20]:
            s = l.strip()
            if meta_re.match(s):
                header_meta_lines.append(s)
                break
        raw_parts = []
        if title:
            raw_parts.append((title + (" " + location if location else "")).strip())
        if header_meta_lines:
            raw_parts.append(header_meta_lines[0])
        if responsibilities:
            raw_parts.append("【" + (resp_label or "岗位职责") + "】")
            raw_parts.append(responsibilities)
        if requirements:
            raw_parts.append("【" + (req_label or "岗位要求") + "】")
            raw_parts.append(requirements)
        if bonus_start > 0:
            raw_parts.append("【加分项】")
            raw_parts.append(block_from(bonus_start, highlight_start if highlight_start > bonus_start else -1).strip())
        if highlight_start > 0:
            raw_parts.append("【岗位亮点】")
            raw_parts.append(block_from(highlight_start, -1).strip())
        detail_raw_text = "\n\n".join([p for p in raw_parts if p]).strip()

        if not title or not detail_raw_text:
            result.http_or_struct_error = f"missing core fields: title={bool(title)}, detail_text_nonempty={bool(detail_raw_text)}; lines_scanned={len(lines)}"
            return result

        # Company name always Tencent per domain
        company_name = "腾讯"
        source_id = postid
        salary = None  # Tencent careers never shows public salary on detail
        record: Dict[str, Any] = {
            "source": "official_career_site",
            "source_company": self.source_company_canonical,
            "source_id": source_id,
            "source_id_method": self.source_id_method_default,
            "source_url": url.strip(),
            "job_title_raw": title.strip(),
            "company_name": company_name,
            "location": location or "",
            "salary": salary,
            "source_education": education,          # None if not visible on page
            "source_experience": experience,        # None if not visible on page
            "publish_time": publish_time,
            "responsibilities": responsibilities or None,
            "requirements": requirements or None,
            "detail_raw_text": detail_raw_text or "",
            "crawl_time": bj_now_iso(),
        }
        record["_sha256"] = sha256_hex(record["responsibilities"] or "", record["requirements"] or "")
        # Validate schema
        missing = [k for k in STANDARD_FIELDS if k not in record]
        if missing:
            result.http_or_struct_error = "adapter schema missing fields: " + ",".join(missing)
            return result
        result.record = record
        result.detail_raw_text_len = len(record["detail_raw_text"] or "")
        result.adapter_parse_success = True
        return result
