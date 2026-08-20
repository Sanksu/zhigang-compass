# -*- coding: utf-8 -*-
"""ByteDance (jobs.bytedance.com) official career site adapter.

Pilot-only.  Pattern-based extraction from browser DOM-text snapshots
(same rationale as Tencent adapter).
"""
from __future__ import annotations
import re
from typing import Dict, Any, Optional
from .base import (
    BaseCareerAdapter, ParseResult, bj_now_iso, sha256_hex, STANDARD_FIELDS
)
from .tencent_adapter import _extract_line_texts  # reuse unwrapper


PATHID_RE = re.compile(r"/position/(\d{19})")
DISPLAYID_RE = re.compile(r"职位\s*ID[::：]\s*([A-Za-z0-9]+)")
DISPLAYID_RE2 = re.compile(r"职位ID[::：]\s*([A-Za-z0-9]+)")


class BytedanceCareerAdapter(BaseCareerAdapter):
    adapter_name = "bytedance_careers"
    source_company_canonical = "ByteDance"
    source_id_method_default = "page_DOM_job_id | URL_path_job_id"

    RESP_LABELS = ("职位描述", "岗位职责", "工作职责", "工作内容", "岗位描述")
    REQ_LABELS = ("职位要求", "任职要求", "岗位要求", "任职资格", "职位任职要求")

    def match(self, url: str) -> bool:
        return bool(url) and ("jobs.bytedance.com" in url) and ("/position/" in url)

    # --- helpers --------------------------------------------------------
    def _extract_ids(self, url: str, snapshot_text: str):
        """Return (display_id, path_id) — whichever is available.

        Per Smoke Test: ByteDance pages expose TWO stable IDs:
          - URL contains /position/<19-digit> (path_id, 19 chars numeric)
          - DOM snapshot displays "职位 ID： A12345" etc. (display_id — alphanum,
            usually starts with A/K and 6-8 chars)
        The Pilot stores source_id = display_id when present (because it
        matches external job-board postings), with source_id_method noting
        that the dual-id exists.
        """
        display_id = None
        path_id = None
        m = PATHID_RE.search(url or "")
        if m:
            path_id = m.group(1)
        for pat in (DISPLAYID_RE, DISPLAYID_RE2):
            m2 = pat.search(snapshot_text or "")
            if m2:
                display_id = m2.group(1)
                break
        return display_id, path_id

    # --- public --------------------------------------------------------
    def build_detail_url(self, stable_id: str):
        # Two forms supported
        if (stable_id or "").isdigit() and len(stable_id) == 19:
            return f"https://jobs.bytedance.com/experienced/position/{stable_id}/detail"
        return None

    def parse(self, url, snapshot_text):
        result = ParseResult()
        result.adapter_name = self.adapter_name
        try:
            return self._parse_impl(url, snapshot_text, result)
        except Exception as exc:  # noqa: BLE001 - defensive
            result.http_or_struct_error = f"adapter_exception: {type(exc).__name__}: {str(exc)[:120]}"
            result.adapter_parse_success = False
            return result

    def _parse_impl(self, url, snapshot_text, result: ParseResult):
        low = (snapshot_text or "").lower()
        if any(k in low for k in ("登录墙", "login wall", "请登录后", "需登录", "需要登录", "captcha", "验证码", "人机验证", "访问验证")):
            if any(k in low for k in ("captcha", "验证码", "人机验证")):
                result.captcha = True; result.http_or_struct_error = "captcha"
            elif any(k in low for k in ("访问验证", "access verification")):
                result.access_verification = True; result.http_or_struct_error = "access_verification"
            else:
                result.login_wall = True; result.http_or_struct_error = "login_wall"
            result.accessible_ok = False
            return result

        display_id, path_id = self._extract_ids(url, snapshot_text)
        if not (display_id or path_id):
            result.http_or_struct_error = "no stable source_id (neither display DOM id nor 19-digit path id found)"
            return result

        lines = _extract_line_texts(snapshot_text)
        if not lines:
            result.http_or_struct_error = "blank snapshot (0 lines)"
            return result

        # 1) Title: single big line BEFORE any "职位描述"/"职位要求" labels,
        #    usually line like "算法工程师 - 今日头条" or "AI Agent研发工程师（模型评测方向） - 移动OS"
        #    no BG metadata.  ByteDance title line generally doesn't embed city (city appears on next meta line)
        title = None
        resp_idx = req_idx = -1
        for i, l in enumerate(lines):
            s = l.strip()
            if resp_idx < 0:
                for lab in self.RESP_LABELS:
                    if s == lab or s.startswith(lab + "（") or s.startswith(lab + "("):
                        resp_idx = i; break
            if req_idx < 0:
                for lab in self.REQ_LABELS:
                    if s == lab or s.startswith(lab + "（") or s.startswith(lab + "("):
                        req_idx = i; break
        # Search title among lines before resp_idx (or first 12 lines if no resp label)
        search_end = resp_idx if resp_idx > 0 else min(15, len(lines))
        for i in range(search_end):
            s = lines[i].strip()
            if not s:
                continue
            if any(sw in s for sw in ("职位描述", "职位要求", "岗位职责", "任职要求", "团队介绍", "投递", "立即投递", "职位 ID", "相关职位", "联系我们", "产品与技术", "校园招聘", "登录", "我们的文化", "成长与回报")):
                continue
            if len(s) < 4 or len(s) > 70:
                continue
            if "社招" in s or "校招" in s or "正式" == s:
                continue
            if s in ("字节跳动", "抖音", "今日头条", "PICO", "TikTok", "火山引擎", "飞书", "豆包"):
                continue
            title = s
            break

        # 2) Location: usually in meta line after title, format: "北京 正式 研发 - 算法 职位 ID： A115691A"  OR "北京、深圳 正式 研发 - 客户端 职位 ID： A219847"
        location = None
        experience = None
        meta_city_re = re.compile(
            r"^\s*((?:(?:北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|重庆|苏州|合肥|郑州|长沙|厦门|青岛|济南|天津|大连|宁波|无锡|东莞|佛山|珠海|贵阳|昆明|哈尔滨|福州|乌鲁木齐|兰州|南宁|南昌|沈阳|石家庄|长春|太原|海口|三亚|呼和浩特|西宁|银川|拉萨|香港|澳门|台北|新加坡|东京|首尔|西雅图|硅谷|纽约|伦敦|悉尼|多伦多|墨尔本)"
            r"(?:[\s、,，/·]+(?:北京|上海|广州|深圳|杭州|成都|南京|武汉|西安|重庆|苏州|合肥|郑州|长沙|厦门|青岛|济南|天津|大连|宁波|无锡|东莞|佛山|珠海|贵阳|昆明|哈尔滨|福州|乌鲁木齐|兰州|南宁|南昌|沈阳|石家庄|长春|太原|海口|三亚|呼和浩特|西宁|银川|拉萨|香港|澳门|台北|新加坡|东京|首尔|西雅图|硅谷|纽约|伦敦|悉尼|多伦多|墨尔本)){0,4}))"
            r"\s+(?:正式|实习|校招|社招)"
        )
        for i, l in enumerate(lines[: min(18, len(lines))]):
            m = meta_city_re.match(l)
            if m:
                location = m.group(1)
                break

        # 3) Responsibilities + Requirements blocks (ByteDance often has "团队介绍：..." inside 职位描述)
        def block_from(start_idx: int, end_idx: int):
            if start_idx < 0:
                return ""
            end = end_idx if end_idx > 0 else len(lines)
            pieces = []
            for j in range(start_idx + 1, min(end, len(lines))):
                s = lines[j].strip()
                if not s: continue
                if any(b in s for b in ("相关职位", "联系我们", "全球招聘", "校园招聘", "投递", "立即投递", "登录", "字节跳动 Seed", "产品与技术", "我们的文化", "成长与回报")):
                    break
                pieces.append(s)
            joined = "\n".join(pieces).strip()
            joined = re.sub(r"\s+(?=\d+\.)", "\n", joined)
            return joined

        end_req = -1
        # Responsibilities end at req_idx; requirements end at footer or 15 lines after
        responsibilities = block_from(resp_idx, req_idx)
        requirements = block_from(req_idx, end_req)

        # Education from requirements
        education = None
        if requirements:
            em = re.search(r"(博士|硕士(?:及以上)?|研究生|本科(?:及以上)?|大专|专科|Ph\.?D|PhD|Master|Bachelor|MBA)", requirements, flags=re.IGNORECASE)
            if em:
                education = em.group(0)

        # Publish time — ByteDance almost never shows publish time on detail page, keep None
        publish_time = None
        salary = None
        company_name = "字节跳动"

        # detail_raw_text: title + meta (if any) + 职责/要求
        raw_parts = []
        if title:
            raw_parts.append(title + ("（" + location + "）" if location else ""))
        meta_line = None
        for l in lines[:18]:
            if "职位 ID：" in l or "职位ID:" in l:
                meta_line = l.strip(); break
        if meta_line:
            raw_parts.append(meta_line)
        if responsibilities:
            lb = None
            for l in self.RESP_LABELS:
                if resp_idx >= 0 and lines[resp_idx].startswith(l): lb = l; break
            raw_parts.append("【" + (lb or "职位描述") + "】\n" + responsibilities)
        if requirements:
            lb = None
            for l in self.REQ_LABELS:
                if req_idx >= 0 and lines[req_idx].startswith(l): lb = l; break
            raw_parts.append("【" + (lb or "职位要求") + "】\n" + requirements)
        detail_raw_text = "\n\n".join([p for p in raw_parts if p]).strip()

        if not title or not detail_raw_text:
            result.http_or_struct_error = f"missing core (title={bool(title)}, raw_text_len={len(detail_raw_text or '')}) / lines_scanned={len(lines)} resp_idx={resp_idx} req_idx={req_idx}"
            return result

        source_id = display_id or path_id
        # Method: if we have both say dual.
        if display_id and path_id:
            method = f"page_DOM_job_id (display {display_id}) | URL_path_job_id (19-digit {path_id})"
        elif display_id:
            method = "page_DOM_job_id"
        else:
            method = "URL_path_job_id"
        record: Dict[str, Any] = {
            "source": "official_career_site",
            "source_company": self.source_company_canonical,
            "source_id": source_id,
            "source_id_method": method,
            "source_url": url.strip(),
            "job_title_raw": title.strip(),
            "company_name": company_name,
            "location": location or "",
            "salary": salary,
            "source_education": education,
            "source_experience": experience,   # usually None for ByteDance public detail pages
            "publish_time": publish_time,
            "responsibilities": responsibilities or None,
            "requirements": requirements or None,
            "detail_raw_text": detail_raw_text or "",
            "crawl_time": bj_now_iso(),
        }
        record["_sha256"] = sha256_hex(record["responsibilities"] or "", record["requirements"] or "")
        missing = [k for k in STANDARD_FIELDS if k not in record]
        if missing:
            result.http_or_struct_error = "adapter schema missing: " + ",".join(missing)
            return result
        result.record = record
        result.detail_raw_text_len = len(record["detail_raw_text"] or "")
        result.adapter_parse_success = True
        return result
